import React, { useState, useCallback, useEffect } from 'react';
import { ImageUploader } from './components/ImageUploader';
import { Editor } from './components/Editor';
import { Header } from './components/Header';
import { ImageCropper } from './components/ImageCropper';
import type { AppState } from './types';
import { AppStateEnum } from './types';

const App: React.FC = () => {
  const [appState, setAppState] = useState<AppState>(AppStateEnum.UPLOADING);
  const [cadastralImage, setCadastralImage] = useState<File | null>(null);
  const [fieldPhoto, setFieldPhoto] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  // State for the resize confirmation modal
  const [pendingFieldPhoto, setPendingFieldPhoto] = useState<File | null>(null);
  const [showResizePrompt, setShowResizePrompt] = useState(false);
  const [largeImageDimensions, setLargeImageDimensions] = useState<{width: number, height: number} | null>(null);
  
  // State for object URLs to prevent memory leaks
  const [cadastralImageUrl, setCadastralImageUrl] = useState<string | null>(null);

  useEffect(() => {
    // Create and manage the object URL for the cadastral image
    if (cadastralImage) {
        const url = URL.createObjectURL(cadastralImage);
        setCadastralImageUrl(url);

        // Cleanup function to revoke the URL when the image changes or unmounts
        return () => {
            URL.revokeObjectURL(url);
        };
    } else {
        // Clear the URL if the image is removed
        setCadastralImageUrl(null);
    }
  }, [cadastralImage]);


  useEffect(() => {
    const handlePaste = (event: ClipboardEvent) => {
        if (appState !== AppStateEnum.UPLOADING) return;

        const items = event.clipboardData?.items;
        if (!items) return;
        
        for (let i = 0; i < items.length; i++) {
            if (items[i].type.indexOf("image") !== -1) {
                const blob = items[i].getAsFile();
                if (blob) {
                    const file = new File([blob], "pasted-image.png", { type: blob.type });
                    setCadastralImage(file);
                    setAppState(AppStateEnum.CROPPING); // Go to cropping after paste
                    event.preventDefault();
                    break;
                }
            }
        }
    };
    
    document.addEventListener('paste', handlePaste);
    return () => {
        document.removeEventListener('paste', handlePaste);
    };
  }, [appState]);


  const handleStartEditing = useCallback(() => {
    if (!cadastralImage || !fieldPhoto) {
      setError("Please upload both images to continue.");
      return;
    }
    setError(null);
    setAppState(AppStateEnum.EDITING);
  }, [cadastralImage, fieldPhoto]);
  
  const handleCadastralImageSelect = (file: File | null) => {
      setCadastralImage(file);
      if(file) {
          setAppState(AppStateEnum.CROPPING);
      }
  }
  
  const handleCropConfirm = (file: File) => {
      setCadastralImage(file);
      setAppState(AppStateEnum.UPLOADING);
  }
  
  const handleCropCancel = () => {
      setAppState(AppStateEnum.UPLOADING);
  }

  const handleReset = () => {
    setAppState(AppStateEnum.UPLOADING);
    setCadastralImage(null);
    setFieldPhoto(null);
    setError(null);
  };
  
  // --- Image Resize Logic ---
  
  const resetResizePrompt = () => {
      setShowResizePrompt(false);
      setPendingFieldPhoto(null);
      setLargeImageDimensions(null);
  };

  const handleResizeConfirm = () => {
      if (!pendingFieldPhoto) return;
      const file = pendingFieldPhoto;

      const reader = new FileReader();
      reader.onload = (e) => {
          const img = new Image();
          img.onload = () => {
              const MAX_DIMENSION = 4000;
              const canvas = document.createElement('canvas');
              const ctx = canvas.getContext('2d');
              if (!ctx) {
                  setFieldPhoto(file);
                  resetResizePrompt();
                  return;
              }

              let newWidth, newHeight;
              if (img.width > img.height) {
                  newWidth = MAX_DIMENSION;
                  newHeight = Math.round((img.height * MAX_DIMENSION) / img.width);
              } else {
                  newHeight = MAX_DIMENSION;
                  newWidth = Math.round((img.width * MAX_DIMENSION) / img.height);
              }

              canvas.width = newWidth;
              canvas.height = newHeight;
              ctx.drawImage(img, 0, 0, newWidth, newHeight);

              canvas.toBlob((blob) => {
                  if (blob) {
                      const resizedFile = new File([blob], file.name, { type: 'image/jpeg', lastModified: Date.now() });
                      setFieldPhoto(resizedFile);
                  } else {
                      setFieldPhoto(file);
                  }
                  resetResizePrompt();
              }, 'image/jpeg', 0.92);
          };
          img.src = e.target?.result as string;
      };
      reader.readAsDataURL(file);
  };

  const handleResizeDecline = () => {
      if (pendingFieldPhoto) {
          setFieldPhoto(pendingFieldPhoto);
      }
      resetResizePrompt();
  };

  const handleResizeCancel = () => {
      setFieldPhoto(null); // This will clear the preview in the controlled ImageUploader
      resetResizePrompt();
  };
  
  const handleFieldPhotoSelect = (file: File | null) => {
      if (!file) {
          setFieldPhoto(null);
          return;
      }

      const reader = new FileReader();
      reader.onload = (e) => {
          const img = new Image();
          img.onload = () => {
              const MAX_DIMENSION = 4000;
              if (img.width > MAX_DIMENSION || img.height > MAX_DIMENSION) {
                  setPendingFieldPhoto(file);
                  setLargeImageDimensions({ width: img.width, height: img.height });
                  setShowResizePrompt(true);
              } else {
                  setFieldPhoto(file);
              }
          };
          img.src = e.target?.result as string;
      };
      reader.readAsDataURL(file);
  };

  const renderContent = () => {
    switch (appState) {
      case AppStateEnum.UPLOADING:
        return (
          <div className="w-full max-w-4xl mx-auto p-8 bg-gray-800 rounded-lg shadow-2xl">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <ImageUploader
                title="1. Upload Cadastral Map"
                description="Upload the line-drawing of the land plot."
                helpText="Drag & Drop, Click, or Paste Image"
                onFileSelect={handleCadastralImageSelect}
                file={cadastralImage}
              />
              <ImageUploader
                title="2. Upload Field Photo"
                description="Upload the photo taken at the site."
                helpText="Drag & Drop or Click to Upload"
                onFileSelect={handleFieldPhotoSelect}
                file={fieldPhoto}
              />
            </div>
            {error && <p className="text-center text-red-400 mt-6">{error}</p>}
            <div className="text-center mt-8">
              <button
                onClick={handleStartEditing}
                disabled={!cadastralImage || !fieldPhoto}
                className="bg-blue-600 text-white font-bold py-3 px-8 rounded-lg shadow-lg hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed transition-all duration-300 transform hover:scale-105"
              >
                Start Editing
              </button>
            </div>
          </div>
        );
      case AppStateEnum.CROPPING:
        if (cadastralImage && cadastralImageUrl) {
            return <ImageCropper 
                imageUrl={cadastralImageUrl} 
                onCrop={handleCropConfirm}
                onCancel={handleCropCancel}
            />
        }
        return null; // Should not happen
      case AppStateEnum.EDITING:
        if (fieldPhoto && cadastralImage && cadastralImageUrl) {
          return (
            <Editor
              fieldPhoto={fieldPhoto}
              overlayImageUrl={cadastralImageUrl}
              onReset={handleReset}
            />
          );
        }
        return null; // Should not happen
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-gray-900 text-gray-200 flex flex-col items-center p-4 sm:p-6">
      <Header onReset={appState === AppStateEnum.EDITING ? handleReset : undefined} />
      <main className="flex-grow flex items-center justify-center w-full">
        {renderContent()}
      </main>
      {showResizePrompt && largeImageDimensions && (
          <div className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50 transition-opacity duration-300" role="dialog" aria-modal="true" aria-labelledby="resize-dialog-title">
              <div className="bg-gray-800 p-8 rounded-lg shadow-2xl text-center w-full max-w-lg mx-4">
                  <h2 id="resize-dialog-title" className="text-2xl font-bold text-blue-300 mb-4">Large Image Detected</h2>
                  <p className="text-gray-300 mb-6">
                      The image dimensions ({largeImageDimensions.width}x{largeImageDimensions.height}) are very large.<br/>
                      For better performance and to prevent upload issues, we recommend resizing it.
                  </p>
                  <div className="flex flex-col sm:flex-row justify-center space-y-3 sm:space-y-0 sm:space-x-4">
                      <button onClick={handleResizeConfirm} className="bg-green-600 text-white font-bold py-2 px-6 rounded-lg shadow-lg hover:bg-green-700 transition-all duration-300">Resize (Recommended)</button>
                      <button onClick={handleResizeDecline} className="bg-gray-600 text-white font-bold py-2 px-6 rounded-lg shadow-lg hover:bg-gray-700 transition-all duration-300">Upload Original</button>
                      <button onClick={handleResizeCancel} className="bg-red-600 text-white font-bold py-2 px-6 rounded-lg shadow-lg hover:bg-red-700 transition-all duration-300">Cancel</button>
                  </div>
              </div>
          </div>
      )}
    </div>
  );
};

export default App;