import React, { useRef, useState, useEffect, useCallback } from 'react';
import type { Point } from '../types';

interface ImageCropperProps {
  imageUrl: string;
  onCrop: (file: File) => void;
  onCancel: () => void;
}

const CROP_RECT_MIN_SIZE = 20;

export const ImageCropper: React.FC<ImageCropperProps> = ({ imageUrl, onCrop, onCancel }) => {
  const imageRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  
  const [cropRect, setCropRect] = useState({ x: 0, y: 0, width: 0, height: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [startPoint, setStartPoint] = useState<Point | null>(null);
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });

  const getMousePos = (e: React.MouseEvent): Point => {
    const rect = containerRef.current!.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    };
  };

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    const pos = getMousePos(e);
    setIsDragging(true);
    setStartPoint(pos);
    setCropRect({ x: pos.x, y: pos.y, width: 0, height: 0 });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging || !startPoint) return;
    const pos = getMousePos(e);
    
    const newWidth = Math.abs(pos.x - startPoint.x);
    const newHeight = Math.abs(pos.y - startPoint.y);
    const newX = Math.min(pos.x, startPoint.x);
    const newY = Math.min(pos.y, startPoint.y);
    
    setCropRect({
        x: Math.max(0, newX),
        y: Math.max(0, newY),
        width: Math.min(newWidth, imageSize.width - newX),
        height: Math.min(newHeight, imageSize.height - newY)
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
    setStartPoint(null);
    if (cropRect.width < CROP_RECT_MIN_SIZE || cropRect.height < CROP_RECT_MIN_SIZE) {
        setCropRect({ x: 0, y: 0, width: 0, height: 0 }); // Reset if too small
    }
  };

  const handleCrop = useCallback(() => {
    if (!imageRef.current || cropRect.width === 0 || cropRect.height === 0) return;
    
    const img = imageRef.current;
    const scaleX = img.naturalWidth / img.width;
    const scaleY = img.naturalHeight / img.height;

    const canvas = document.createElement('canvas');
    canvas.width = cropRect.width * scaleX;
    canvas.height = cropRect.height * scaleY;
    const ctx = canvas.getContext('2d');

    if (!ctx) {
      console.error("Could not get canvas context");
      return;
    }

    ctx.drawImage(
      img,
      cropRect.x * scaleX,
      cropRect.y * scaleY,
      cropRect.width * scaleX,
      cropRect.height * scaleY,
      0, 0,
      canvas.width,
      canvas.height
    );

    canvas.toBlob((blob) => {
      if (blob) {
        const croppedFile = new File([blob], 'cropped_cadastral.png', { type: 'image/png' });
        onCrop(croppedFile);
      }
    }, 'image/png');
  }, [cropRect, onCrop]);
  
  const onImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const { width, height } = e.currentTarget;
    setImageSize({ width, height });
  }

  return (
    <div className="w-full max-w-4xl mx-auto p-8 bg-gray-800 rounded-lg shadow-2xl flex flex-col items-center">
        <h2 className="text-2xl font-bold text-blue-300 mb-2">Crop Cadastral Map</h2>
        <p className="text-gray-400 mb-6">Drag on the image to select the area you want to use.</p>
        <div 
            ref={containerRef}
            className="relative cursor-crosshair select-none"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp} // Stop dragging if mouse leaves container
        >
            <img ref={imageRef} src={imageUrl} onLoad={onImageLoad} alt="Cadastral map to crop" className="max-w-full max-h-[60vh] object-contain" />
            {cropRect.width > 0 && cropRect.height > 0 && (
                <div 
                    className="absolute border-2 border-dashed border-blue-400 bg-blue-500 bg-opacity-25 pointer-events-none"
                    style={{
                        left: cropRect.x,
                        top: cropRect.y,
                        width: cropRect.width,
                        height: cropRect.height,
                    }}
                />
            )}
        </div>
        <div className="flex items-center space-x-4 mt-6">
            <button
                onClick={onCancel}
                className="bg-gray-600 text-white font-bold py-2 px-6 rounded-lg shadow-lg hover:bg-gray-700 transition-all duration-300"
            >
                Cancel
            </button>
            <button
                onClick={handleCrop}
                disabled={cropRect.width < CROP_RECT_MIN_SIZE || cropRect.height < CROP_RECT_MIN_SIZE}
                className="bg-green-600 text-white font-bold py-2 px-6 rounded-lg shadow-lg hover:bg-green-700 disabled:bg-gray-500 disabled:cursor-not-allowed transition-all duration-300"
            >
                Confirm Crop
            </button>
        </div>
    </div>
  );
};