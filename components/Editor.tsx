import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import type { Point, ColorFilter } from '../types';
import { DraggablePoint } from './DraggablePoint';
import { ColorFilterModal } from './ColorFilterModal';

// This lets TypeScript know that 'piexif' is available as a global variable
// after being loaded by the script tag in index.html.
declare var piexif: any;

// Solves a system of linear equations A * x = b using Gaussian elimination with partial pivoting.
const solveSystem = (A: number[][], b: number[]): number[] | null => {
    const n = A.length;
    const augmentedMatrix: number[][] = A.map((row, i) => [...row, b[i]]);

    for (let i = 0; i < n; i++) {
        let maxRow = i;
        for (let k = i + 1; k < n; k++) {
            if (Math.abs(augmentedMatrix[k][i]) > Math.abs(augmentedMatrix[maxRow][i])) {
                maxRow = k;
            }
        }
        [augmentedMatrix[i], augmentedMatrix[maxRow]] = [augmentedMatrix[maxRow], augmentedMatrix[i]];

        if (Math.abs(augmentedMatrix[i][i]) < 1e-9) return null; // No unique solution

        for (let k = i + 1; k < n; k++) {
            const factor = -augmentedMatrix[k][i] / augmentedMatrix[i][i];
            for (let j = i; j < n + 1; j++) {
                if (i === j) {
                    augmentedMatrix[k][j] = 0;
                } else {
                    augmentedMatrix[k][j] += factor * augmentedMatrix[i][j];
                }
            }
        }
    }

    const x = new Array(n).fill(0);
    for (let i = n - 1; i >= 0; i--) {
        x[i] = augmentedMatrix[i][n] / augmentedMatrix[i][i];
        for (let k = i - 1; k >= 0; k--) {
            augmentedMatrix[k][n] -= augmentedMatrix[k][i] * x[i];
        }
    }
    return x;
};

// Computes the perspective transform matrix to map src points to dst points
// and returns a CSS matrix3d string.
const getPerspectiveTransform = (src: Point[], dst: Point[]): string => {
    if (src.length !== 4 || dst.length !== 4) return 'none';
    const A: number[][] = [];
    const b: number[] = [];
    for (let i = 0; i < 4; i++) {
        const { x, y } = src[i];
        const { x: xp, y: yp } = dst[i];
        A.push([x, y, 1, 0, 0, 0, -x * xp, -y * xp]);
        b.push(xp);
        A.push([0, 0, 0, x, y, 1, -x * yp, -y * yp]);
        b.push(yp);
    }

    const h = solveSystem(A, b);
    if (!h) return 'none';
    
    const matrix = [
        h[0], h[3], 0, h[6],
        h[1], h[4], 0, h[7],
        0   , 0   , 1, 0,
        h[2], h[5], 0, 1
    ];
    
    return `matrix3d(${matrix.join(',')})`;
};

// Color processing utilities
const hexToRgb = (hex: string): { r: number; g: number; b: number } | null => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result
        ? { r: parseInt(result[1], 16), g: parseInt(result[2], 16), b: parseInt(result[3], 16) }
        : null;
};

const colorDistance = (rgb1: { r: number; g: number; b: number }, rgb2: { r: number; g: number; b: number }): number => {
    return Math.sqrt(Math.pow(rgb1.r - rgb2.r, 2) + Math.pow(rgb1.g - rgb2.g, 2) + Math.pow(rgb1.b - rgb2.b, 2));
};

const processImageColors = (
    imageUrl: string,
    filters: { keep: ColorFilter[]; remove: ColorFilter[] }
): Promise<string> => {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.crossOrigin = 'Anonymous';
        img.onload = () => {
            const canvas = document.createElement('canvas');
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            if (!ctx) return reject(new Error('Could not get canvas context'));
            
            ctx.drawImage(img, 0, 0);
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
            const data = imageData.data;

            const keepRgbs = filters.keep.map(f => ({ ...hexToRgb(f.color)!, targetColor: hexToRgb(f.color)!, tolerance: f.tolerance * 2.55 }));
            const removeRgbs = filters.remove.map(f => ({ ...hexToRgb(f.color)!, tolerance: f.tolerance * 2.55 }));

            for (let i = 0; i < data.length; i += 4) {
                const pixelRgb = { r: data[i], g: data[i + 1], b: data[i + 2] };
                
                let isRemoved = false;
                for (const remove of removeRgbs) {
                    if (colorDistance(pixelRgb, remove) < remove.tolerance) {
                        isRemoved = true;
                        break;
                    }
                }

                if (isRemoved) {
                    data[i + 3] = 0; // Make transparent
                    continue;
                }
                
                let isKept = false;
                if (keepRgbs.length > 0) {
                    for (const keep of keepRgbs) {
                        if (colorDistance(pixelRgb, keep) < keep.tolerance) {
                            // Recolor the pixel to the exact target color
                            data[i] = keep.targetColor.r;
                            data[i + 1] = keep.targetColor.g;
                            data[i + 2] = keep.targetColor.b;
                            data[i + 3] = 255; // Ensure it's fully opaque
                            isKept = true;
                            break; // Matched one, don't need to check others
                        }
                    }
                    if (!isKept) {
                        data[i + 3] = 0; // Make transparent if it didn't match any keep filter
                    }
                }
            }
            ctx.putImageData(imageData, 0, 0);
            resolve(canvas.toDataURL('image/png'));
        };
        img.onerror = (err) => reject(err);
        img.src = imageUrl;
    });
};


interface EditorProps {
  fieldPhoto: File;
  overlayImageUrl: string;
  onReset: () => void;
}

type EditMode = 'manual' | 'auto';
interface ImageGeom { x: number; y: number; width: number; height: number; }

const cornerNames = ['Top-Left', 'Top-Right', 'Bottom-Right', 'Bottom-Left'];
const autoPinInstructions = [
  ...cornerNames.flatMap(name => [
    `Click ${name} on CADASTRAL MAP`, 
    `Click corresponding ${name} on FIELD PHOTO`
  ])
];

const calculateObjectContainGeometry = (containerWidth: number, containerHeight: number, imgNaturalWidth: number, imgNaturalHeight: number): ImageGeom => {
    const imgRatio = imgNaturalWidth / imgNaturalHeight;
    const containerRatio = containerWidth / containerHeight;
    let renderedWidth, renderedHeight, xOffset, yOffset;

    if (imgRatio > containerRatio) { // Image is wider than container
        renderedWidth = containerWidth;
        renderedHeight = renderedWidth / imgRatio;
        xOffset = 0;
        yOffset = (containerHeight - renderedHeight) / 2;
    } else { // Image is taller or same ratio as container
        renderedHeight = containerHeight;
        renderedWidth = renderedHeight * imgRatio;
        xOffset = (containerWidth - renderedWidth) / 2;
        yOffset = 0;
    }
    return { x: xOffset, y: yOffset, width: renderedWidth, height: renderedHeight };
};

export const Editor: React.FC<EditorProps> = ({ fieldPhoto, overlayImageUrl, onReset }) => {
  const fieldPhotoUrl = useMemo(() => URL.createObjectURL(fieldPhoto), [fieldPhoto]);
  // Common State
  const [opacity, setOpacity] = useState(0.7);
  const [lineThickness, setLineThickness] = useState(0);
  const [transform, setTransform] = useState('none');
  const [editMode, setEditMode] = useState<EditMode>('manual');
  const [saveWithOverlay, setSaveWithOverlay] = useState(true);
  
  // Color Filtering State
  const [isColorModalOpen, setIsColorModalOpen] = useState(false);
  const [isProcessingOverlay, setIsProcessingOverlay] = useState(true);
  const [processedOverlayUrl, setProcessedOverlayUrl] = useState<string | null>(null);
  const [colorFilters, setColorFilters] = useState<{ keep: ColorFilter[], remove: ColorFilter[] }>({
    keep: [{ color: '#ff0000', tolerance: 80 }],
    remove: [{ color: '#ffff00', tolerance: 80 }, { color: '#ffffff', tolerance: 5 }],
  });

  // DOM Refs and Dims
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerRect, setContainerRect] = useState<DOMRect | null>(null);
  const overlayPreviewRef = useRef<HTMLDivElement>(null);
  const [overlayImageSize, setOverlayImageSize] = useState<{width: number; height: number} | null>(null);
  const [imageRenderGeom, setImageRenderGeom] = useState<ImageGeom>({ x: 0, y: 0, width: 0, height: 0 });
  const [overlayPreviewGeom, setOverlayPreviewGeom] = useState<ImageGeom>({ x: 0, y: 0, width: 0, height: 0 });
  
  // Points State
  const [destPoints, setDestPoints] = useState<Point[]>([]); // Points on field photo
  const [sourcePoints, setSourcePoints] = useState<Point[]>([]); // Points on cadastral map
  
  // Mode-specific State
  const [draggingPointIndex, setDraggingPointIndex] = useState<number | null>(null);
  const [draggingSourcePointIndex, setDraggingSourcePointIndex] = useState<number | null>(null);
  const [pinningStep, setPinningStep] = useState(0); // For auto mode
  const [manualScale, setManualScale] = useState(0.5); // For manual mode initial points scale
  const [isManuallyAdjusted, setIsManuallyAdjusted] = useState(false);

  // Effect to process overlay image when filters or source image change
  useEffect(() => {
    if (!overlayImageUrl) return;
    setIsProcessingOverlay(true);
    processImageColors(overlayImageUrl, colorFilters)
        .then(dataUrl => setProcessedOverlayUrl(dataUrl))
        .catch(err => {
            console.error("Color processing failed:", err);
            // Fallback to original image on error
            setProcessedOverlayUrl(overlayImageUrl);
        })
        .finally(() => setIsProcessingOverlay(false));
  }, [overlayImageUrl, colorFilters]);


  const updatePointsForScale = useCallback((scale: number) => {
    if (!imageRenderGeom || imageRenderGeom.width === 0 || !overlayImageSize) return;

    const geom = imageRenderGeom;
    const overlayAspect = overlayImageSize.width / overlayImageSize.height;

    let scaledWidth, scaledHeight;
    
    // Fit the overlay (at the given scale) inside the geom, preserving aspect ratio
    const containerAspect = geom.width / geom.height;
    if (overlayAspect > containerAspect) {
        scaledWidth = geom.width * scale;
        scaledHeight = scaledWidth / overlayAspect;
    } else {
        scaledHeight = geom.height * scale;
        scaledWidth = scaledHeight * overlayAspect;
    }

    const centerX = geom.x + geom.width / 2;
    const centerY = geom.y + geom.height / 2;

    const newPoints = [
        { x: centerX - scaledWidth / 2, y: centerY - scaledHeight / 2 },
        { x: centerX + scaledWidth / 2, y: centerY - scaledHeight / 2 },
        { x: centerX + scaledWidth / 2, y: centerY + scaledHeight / 2 },
        { x: centerX - scaledWidth / 2, y: centerY + scaledHeight / 2 },
    ];
    setDestPoints(newPoints);
  }, [imageRenderGeom, overlayImageSize]);

  // Load overlay image dimensions
  useEffect(() => {
    const img = new Image();
    img.src = overlayImageUrl;
    img.onload = () => setOverlayImageSize({ width: img.naturalWidth, height: img.naturalHeight });
  }, [overlayImageUrl]);

  // Handle container resize and calculate geometries
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
        if (entries[0]) setContainerRect(entries[0].target.getBoundingClientRect());
    });
    if (containerRef.current) observer.observe(containerRef.current);
    
    const previewEl = overlayPreviewRef.current;
    if(previewEl && overlayImageSize){
        const previewRect = previewEl.getBoundingClientRect();
        const geom = calculateObjectContainGeometry(previewRect.width, previewRect.height, overlayImageSize.width, overlayImageSize.height);
        setOverlayPreviewGeom(geom);
    }
    
    return () => observer.disconnect();
  }, [overlayImageSize, containerRect]);
  
  useEffect(() => {
    const img = new Image();
    img.src = fieldPhotoUrl;
    img.onload = () => {
        if (!containerRect) return;
        const geom = calculateObjectContainGeometry(containerRect.width, containerRect.height, img.naturalWidth, img.naturalHeight);
        setImageRenderGeom(geom);
    };
  }, [fieldPhotoUrl, containerRect]);
  
  // Effect for updating points based on scale/reset, but only if not manually adjusted
  useEffect(() => {
    if (editMode === 'manual' && imageRenderGeom.width > 0 && overlayImageSize && !isManuallyAdjusted) {
      updatePointsForScale(manualScale);
    }
  }, [editMode, imageRenderGeom, overlayImageSize, manualScale, isManuallyAdjusted, updatePointsForScale]);

  // Effect for setting source points for manual mode
  useEffect(() => {
    if (editMode === 'manual' && overlayImageSize) {
      setSourcePoints([
        { x: 0, y: 0 },
        { x: overlayImageSize.width, y: 0 },
        { x: overlayImageSize.width, y: overlayImageSize.height },
        { x: 0, y: overlayImageSize.height },
      ]);
    }
  }, [editMode, overlayImageSize]);

  // Effect to clear points when switching to Auto Pin mode
  useEffect(() => {
    if (editMode === 'auto') {
      setSourcePoints([]);
      setDestPoints([]);
    }
  }, [editMode]);

  // Calculate transform when points change
  useEffect(() => {
    if (destPoints.length === 4 && sourcePoints.length === 4 && overlayImageSize) {
        const newTransform = getPerspectiveTransform(sourcePoints, destPoints);
        setTransform(newTransform);
    } else {
        setTransform('none');
    }
  }, [destPoints, sourcePoints, overlayImageSize]);

  // Effect to handle dragging of source points in Auto Pin mode
  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (draggingSourcePointIndex === null || !overlayPreviewRef.current || !overlayImageSize || !overlayPreviewGeom) return;

      const rect = overlayPreviewRef.current.getBoundingClientRect();
      let clickX = e.clientX - rect.left;
      let clickY = e.clientY - rect.top;

      // Clamp to the image bounds within the preview container
      clickX = Math.max(overlayPreviewGeom.x, Math.min(clickX, overlayPreviewGeom.x + overlayPreviewGeom.width));
      clickY = Math.max(overlayPreviewGeom.y, Math.min(clickY, overlayPreviewGeom.y + overlayPreviewGeom.height));

      const relativeX = clickX - overlayPreviewGeom.x;
      const relativeY = clickY - overlayPreviewGeom.y;

      const newPoint = {
        x: (relativeX / overlayPreviewGeom.width) * overlayImageSize.width,
        y: (relativeY / overlayPreviewGeom.height) * overlayImageSize.height
      };

      setSourcePoints(prev => prev.map((p, i) => i === draggingSourcePointIndex ? newPoint : p));
    };

    const handleMouseUp = () => {
      if (draggingSourcePointIndex === null) return;
      setDraggingSourcePointIndex(null);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    };

    if (draggingSourcePointIndex !== null) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      if (draggingSourcePointIndex !== null) {
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
      }
    };
  }, [draggingSourcePointIndex, overlayImageSize, overlayPreviewGeom]);

  // Cleanup object URL when component unmounts
  useEffect(() => {
    return () => {
        URL.revokeObjectURL(fieldPhotoUrl);
    }
  }, [fieldPhotoUrl]);

  const handleModeChange = (mode: EditMode) => {
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
    
    setEditMode(mode);
    setTransform('none');
    setPinningStep(0);
    setIsManuallyAdjusted(false);
  };

  const handleManualScaleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newScale = parseFloat(e.target.value);
    setIsManuallyAdjusted(false);
    setManualScale(newScale);
  };

  const handleManualPointDrag = (newPoint: Point, draggedIndex: number) => {
    setIsManuallyAdjusted(true);
    setDestPoints(currentPoints => 
        currentPoints.map((point, index) => 
            index === draggedIndex ? newPoint : point
        )
    );
  }
  
  const handleAutoDestPointDrag = (newPoint: Point, draggedIndex: number) => {
    setDestPoints(currentPoints =>
      currentPoints.map((point, index) =>
        index === draggedIndex ? newPoint : point
      )
    );
  };

  const handleSourcePointMouseDown = (e: React.MouseEvent, index: number) => {
    e.stopPropagation();
    // Prevent dragging source points while waiting for a destination point click
    if (pinningStep % 2 !== 0) return;
    setDraggingSourcePointIndex(index);
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'grabbing';
  };

  const handleAutoPinClick = (e: React.MouseEvent, target: 'source' | 'dest') => {
    if (pinningStep >= 8) return;

    if (target === 'source' && overlayPreviewRef.current && overlayImageSize) {
      const rect = overlayPreviewRef.current.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;
      
      if (clickX < overlayPreviewGeom.x || clickX > overlayPreviewGeom.x + overlayPreviewGeom.width || clickY < overlayPreviewGeom.y || clickY > overlayPreviewGeom.y + overlayPreviewGeom.height) return;
      
      const relativeX = clickX - overlayPreviewGeom.x;
      const relativeY = clickY - overlayPreviewGeom.y;
      
      const point = {
        x: (relativeX / overlayPreviewGeom.width) * overlayImageSize.width,
        y: (relativeY / overlayPreviewGeom.height) * overlayImageSize.height
      };
      
      if (pinningStep % 2 === 0) {
        setSourcePoints(prev => [...prev, point]);
        setPinningStep(prev => prev + 1);
      }
    } else if (target === 'dest') {
      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
      const point = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      if (pinningStep % 2 !== 0) {
        setDestPoints(prev => [...prev, point]);
        setPinningStep(prev => prev + 1);
      }
    }
  };

  const handleContainerClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (editMode !== 'auto' || pinningStep % 2 === 0 || pinningStep >= 8) return;
    if (overlayPreviewRef.current && overlayPreviewRef.current.contains(e.target as Node)) return;
    handleAutoPinClick(e, 'dest');
  };

  const handleSave = useCallback(async () => {
    let exifBytes: string | null = null;
    try {
        const getExifFromFile = (file: File): Promise<string | null> =>
            new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = (e) => {
                    try {
                        const arrayBuffer = e.target?.result as ArrayBuffer;
                        if (!arrayBuffer) { resolve(null); return; }
                        const bytes = new Uint8Array(arrayBuffer);
                        let binary = '';
                        for (let i = 0; i < bytes.byteLength; i++) {
                            binary += String.fromCharCode(bytes[i]);
                        }
                        const exifObj = piexif.load(binary);
                        if (Object.keys(exifObj['0th']).length > 0 || Object.keys(exifObj.Exif).length > 0 || Object.keys(exifObj.GPS).length > 0) {
                            resolve(piexif.dump(exifObj));
                        } else {
                            resolve(null);
                        }
                    } catch (err) {
                        console.log("Could not load EXIF data from file.", err);
                        resolve(null);
                    }
                };
                reader.onerror = (e) => {
                    console.warn("Could not read file for EXIF.", e);
                    resolve(null);
                };
                reader.readAsArrayBuffer(file);
            });
        exifBytes = await getExifFromFile(fieldPhoto);
    } catch (e) {
        console.warn("An error occurred during EXIF data extraction.", e);
    }

    const bgImage = new Image();
    bgImage.crossOrigin = "anonymous";
    bgImage.src = fieldPhotoUrl;
    
    bgImage.onload = async () => {
        const canvas = document.createElement('canvas');
        canvas.width = bgImage.naturalWidth;
        canvas.height = bgImage.naturalHeight;
        const ctx = canvas.getContext('2d');
        if (!ctx || !imageRenderGeom || !overlayImageSize) {
          alert("Could not prepare image for saving. Missing context or dimensions.");
          return;
        }

        ctx.drawImage(bgImage, 0, 0);

        const triggerDownload = (data: Blob | string, filename: string) => {
            const link = document.createElement('a');
            link.download = filename;
            let url: string | null = null;
            if (data instanceof Blob) {
                url = URL.createObjectURL(data);
                link.href = url;
            } else {
                link.href = data;
            }
            link.click();
            if (url) {
                URL.revokeObjectURL(url);
            }
        };
        
        const getCanvasBlob = (canvas: HTMLCanvasElement): Promise<Blob | null> => {
            return new Promise(resolve => {
                canvas.toBlob(blob => resolve(blob), 'image/jpeg', 0.9);
            });
        };

        if (!saveWithOverlay) {
            if (exifBytes) {
                const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
                const finalDataUrl = piexif.insert(exifBytes, dataUrl);
                triggerDownload(finalDataUrl, 'landviewer-original.jpeg');
            } else {
                const blob = await getCanvasBlob(canvas);
                if (blob) {
                    triggerDownload(blob, 'landviewer-original.jpeg');
                } else {
                    alert('Failed to create image blob.');
                }
            }
            return;
        }
        
        if (!processedOverlayUrl) {
            alert("Overlay image is not ready. Please wait.");
            return;
        }

        const scale = bgImage.naturalWidth / imageRenderGeom.width;
        const finalDestPoints = destPoints.map(p => ({
            x: (p.x - imageRenderGeom.x) * scale,
            y: (p.y - imageRenderGeom.y) * scale
        }));

        if(finalDestPoints.length < 4 || sourcePoints.length < 4) {
            alert("Please align the overlay before saving."); return;
        }

        const finalTransform = getPerspectiveTransform(sourcePoints, finalDestPoints);
        if(finalTransform === 'none') {
            alert("Could not calculate final transform for saving."); return;
        }
        
        const thicknessFilterDef = lineThickness > 0 ? `<filter id="line-thickness-filter-save"><feMorphology operator="dilate" radius="${lineThickness}" /></filter>` : '';
        const filterStyle = lineThickness > 0 ? `filter: url(#line-thickness-filter-save);` : '';

        const svgString = `
            <svg xmlns="http://www.w3.org/2000/svg" width="${bgImage.naturalWidth}" height="${bgImage.naturalHeight}">
                <defs>${thicknessFilterDef}</defs>
                <foreignObject x="0" y="0" width="${overlayImageSize.width}" height="${overlayImageSize.height}" style="transform-origin: 0 0; transform: ${finalTransform};">
                    <div xmlns="http://www.w3.org/1999/xhtml" style="width:${overlayImageSize.width}px; height:${overlayImageSize.height}px; opacity: ${opacity};">
                        <img src="${processedOverlayUrl}" style="width:100%; height:100%; ${filterStyle}" />
                    </div>
                </foreignObject>
            </svg>`;
        
        const svgImage = new Image();
        
        const cleanup = () => {
            URL.revokeObjectURL(svgImage.src);
        };

        svgImage.onload = async () => {
            try {
                ctx.drawImage(svgImage, 0, 0);
                if (exifBytes) {
                    const dataUrl = canvas.toDataURL('image/jpeg', 0.9);
                    const finalDataUrl = piexif.insert(exifBytes, dataUrl);
                    triggerDownload(finalDataUrl, 'landviewer-result.jpeg');
                } else {
                    const blob = await getCanvasBlob(canvas);
                    if (blob) {
                        triggerDownload(blob, 'landviewer-result.jpeg');
                    } else {
                       alert('Failed to create final image blob.');
                    }
                }
            } catch (e) {
                console.error("Error during final image generation:", e);
                alert("An error occurred while saving the image.");
            } finally {
                cleanup();
            }
        };
        svgImage.onerror = () => {
            alert("An error occurred while preparing the overlay for saving.");
            cleanup();
        };
        
        const svgBlob = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' });
        svgImage.src = URL.createObjectURL(svgBlob);
    }
    bgImage.onerror = () => alert("Failed to load background image for saving.");
  }, [fieldPhoto, fieldPhotoUrl, processedOverlayUrl, opacity, lineThickness, imageRenderGeom, overlayImageSize, destPoints, sourcePoints, saveWithOverlay]);

  const overlayFilterStyle = lineThickness > 0 ? 'url(#line-thickness-filter)' : 'none';

  return (
    <div className="w-full h-[85vh] flex flex-col items-center">
      <svg style={{ position: 'absolute', height: 0, width: 0 }}>
        <defs>
          <filter id="line-thickness-filter">
            <feMorphology operator="dilate" radius={lineThickness} />
          </filter>
        </defs>
      </svg>
      {isColorModalOpen && (
          <ColorFilterModal
            initialFilters={colorFilters}
            onApply={(newFilters) => {
                setColorFilters(newFilters);
                setIsColorModalOpen(false);
            }}
            onClose={() => setIsColorModalOpen(false)}
          />
      )}
      <div 
        ref={containerRef} 
        className={`relative w-full max-w-7xl h-full rounded-lg overflow-hidden shadow-2xl bg-gray-800 ${editMode === 'auto' && pinningStep < 8 && pinningStep % 2 !== 0 ? 'cursor-crosshair' : ''}`}
        onClick={handleContainerClick}
      >
        <img src={fieldPhotoUrl} alt="Field" className="w-full h-full object-contain" />
        
        {editMode === 'auto' && (
          <>
            {pinningStep < 8 && (
                <div className="absolute inset-0 bg-black bg-opacity-60 flex items-center justify-center pointer-events-none z-25">
                    <p className="text-2xl font-bold text-white drop-shadow-lg animate-pulse pointer-events-none">
                      {autoPinInstructions[pinningStep]}
                    </p>
                </div>
            )}
            <div 
              ref={overlayPreviewRef}
              className={`absolute top-4 left-4 w-1/4 max-w-[200px] bg-gray-900 border-2 rounded-lg shadow-lg z-20 ${pinningStep < 8 && pinningStep % 2 === 0 ? 'border-blue-400 animate-pulse cursor-crosshair' : 'border-gray-600'}`} 
              onClick={(e) => { e.stopPropagation(); handleAutoPinClick(e, 'source'); }}
            >
              <img src={overlayImageUrl} alt="Cadastral Map" className="w-full h-full object-contain pointer-events-none"/>
              {sourcePoints.map((p, i) => (
                <div 
                  key={`src-${i}`} 
                  onMouseDown={(e) => handleSourcePointMouseDown(e, i)}
                  className="absolute w-4 h-4 -translate-x-1/2 -translate-y-1/2 bg-yellow-400 border-2 border-white rounded-full cursor-grab active:cursor-grabbing shadow-md z-30" 
                  style={{ left: `${overlayPreviewGeom.x + (p.x / (overlayImageSize?.width || 1)) * overlayPreviewGeom.width}px`, top: `${overlayPreviewGeom.y + (p.y / (overlayImageSize?.height || 1)) * overlayPreviewGeom.height}px` }}/>
              ))}
            </div>
          </>
        )}

        {overlayImageSize && processedOverlayUrl && (
           <img 
             src={processedOverlayUrl} 
             alt="Cadastral Overlay"
             style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: overlayImageSize.width,
                height: overlayImageSize.height,
                transformOrigin: '0 0',
                transform: transform,
                opacity: (destPoints.length === 4 && !isProcessingOverlay) ? opacity : 0,
                pointerEvents: 'none',
                transition: 'opacity 0.3s ease-in-out',
                filter: overlayFilterStyle
             }}
           />
        )}
        {isProcessingOverlay && (
            <div className="absolute inset-0 bg-black bg-opacity-50 flex items-center justify-center z-30">
                <p className="text-white text-lg animate-pulse">Processing colors...</p>
            </div>
        )}
        {editMode === 'manual' && destPoints.map((p, i) => (
          <DraggablePoint
            key={`dest-${i}`}
            point={p}
            onDrag={(newPoint) => handleManualPointDrag(newPoint, i)}
            onDragStart={() => setDraggingPointIndex(i)}
            onDragEnd={() => setDraggingPointIndex(null)}
            containerRect={containerRect}
          />
        ))}
         {editMode === 'auto' && destPoints.map((p, i) => (
            <DraggablePoint
                key={`dest-pin-${i}`}
                point={p}
                onDrag={(newPoint) => handleAutoDestPointDrag(newPoint, i)}
                onDragStart={() => setDraggingPointIndex(i)}
                onDragEnd={() => setDraggingPointIndex(null)}
                containerRect={containerRect}
                className="absolute w-4 h-4 -translate-x-1/2 -translate-y-1/2 bg-red-500 border-2 border-white rounded-full cursor-grab active:cursor-grabbing shadow-lg z-10"
            >
              <span className="absolute -bottom-6 left-1/2 -translate-x-1/2 text-white font-bold text-sm bg-black bg-opacity-50 px-1.5 rounded select-none pointer-events-none">{i + 1}</span>
            </DraggablePoint>
        ))}
      </div>
      <div className="w-full max-w-5xl mt-4 p-4 bg-gray-800 rounded-lg shadow-lg flex items-center justify-between space-x-6">
         <div className="flex items-center space-x-4">
            <span className="font-semibold text-gray-300">Mode:</span>
            <div className="flex rounded-lg bg-gray-700 p-1">
                <button onClick={() => handleModeChange('manual')} className={`px-3 py-1 text-sm font-medium rounded-md transition-colors ${editMode === 'manual' ? 'bg-blue-600 text-white' : 'text-gray-300 hover:bg-gray-600'}`}>Manual Adjust</button>
                <button onClick={() => handleModeChange('auto')} className={`px-3 py-1 text-sm font-medium rounded-md transition-colors ${editMode === 'auto' ? 'bg-blue-600 text-white' : 'text-gray-300 hover:bg-gray-600'}`}>Auto Pin</button>
            </div>
            {editMode === 'auto' && (
                <button onClick={() => { setSourcePoints([]); setDestPoints([]); setPinningStep(0); }} className="bg-yellow-600 text-white font-bold py-1.5 px-3 rounded-lg text-sm hover:bg-yellow-700 transition-colors">Clear Pins</button>
            )}
            {editMode === 'manual' && (
              <div className="flex items-center space-x-2">
                <label htmlFor="scale" className="text-sm font-medium text-gray-300">Initial Scale:</label>
                <input id="scale" type="range" min="0.1" max="1.2" step="0.05" value={manualScale} onChange={handleManualScaleChange} className="w-24 cursor-pointer accent-blue-500"/>
                <button onClick={() => { setManualScale(0.5); setIsManuallyAdjusted(false); }} className="bg-indigo-600 text-white font-bold py-1.5 px-3 rounded-lg text-sm hover:bg-indigo-700 transition-colors">Reset Points</button>
              </div>
            )}
        </div>
        <div className="flex-grow flex justify-center items-center space-x-4">
            <button onClick={() => setIsColorModalOpen(true)} className="bg-purple-600 text-white font-semibold py-1.5 px-4 rounded-lg text-sm hover:bg-purple-700 transition-colors shadow-md">
                Color Filters
            </button>
            <div className="flex items-center space-x-2">
              <label htmlFor="thickness" className="font-semibold text-gray-300">Thickness:</label>
              <input id="thickness" type="range" min="0" max="3" step="0.1" value={lineThickness} onChange={(e) => setLineThickness(parseFloat(e.target.value))} className="w-32 cursor-pointer accent-blue-500" />
            </div>
            <div className="flex items-center space-x-2">
              <label htmlFor="opacity" className="font-semibold text-gray-300">Opacity:</label>
              <input id="opacity" type="range" min="0" max="1" step="0.05" value={opacity} onChange={(e) => setOpacity(parseFloat(e.target.value))} className="w-32 cursor-pointer accent-blue-500" />
            </div>
        </div>
        <div className="flex items-center space-x-4">
            <div className="flex items-center space-x-2">
              <input id="save-overlay-toggle" type="checkbox" checked={saveWithOverlay} onChange={(e) => setSaveWithOverlay(e.target.checked)} className="w-4 h-4 rounded accent-green-500 cursor-pointer" />
              <label htmlFor="save-overlay-toggle" className="font-semibold text-gray-300 cursor-pointer select-none">Include Overlay</label>
            </div>
            <button onClick={handleSave} disabled={destPoints.length < 4} className="bg-green-600 text-white font-bold py-2 px-6 rounded-lg shadow-md hover:bg-green-700 transition-colors flex items-center space-x-2 disabled:bg-gray-600 disabled:cursor-not-allowed">
              {/* FIX: Corrected the malformed d attribute in the SVG path. An invalid flag value of '11' was present due to a missing space, causing a cascading parsing error. */}
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor"> <path d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zM6.293 6.707a1 1 0 010-1.414l3-3a1 1 0 011.414 0l3 3a1 1 0 01-1.414 1.414L11 5.414V13a1 1 0 11-2 0V5.414L7.707 6.707a1 1 0 01-1.414 0z" /> </svg>
              <span>Save Image</span>
            </button>
        </div>
      </div>
    </div>
  );
};
