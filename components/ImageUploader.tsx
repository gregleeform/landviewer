import React, { useCallback, useEffect, useMemo } from 'react';

interface ImageUploaderProps {
  title: string;
  description: string;
  helpText: string;
  onFileSelect: (file: File | null) => void;
  file: File | null;
}

export const ImageUploader: React.FC<ImageUploaderProps> = ({ title, description, helpText, onFileSelect, file }) => {
  const preview = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);

  // Cleanup object URL when it changes or component unmounts
  useEffect(() => {
    return () => {
      if (preview) {
        URL.revokeObjectURL(preview);
      }
    };
  }, [preview]);

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = event.target.files?.[0] || null;
    onFileSelect(selectedFile);
  };
  
  const handleRemove = () => {
    onFileSelect(null);
  }

  const onDragOver = useCallback((event: React.DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
  }, []);

  const onDrop = useCallback((event: React.DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    const droppedFile = event.dataTransfer.files?.[0] || null;
    onFileSelect(droppedFile);
  }, [onFileSelect]);

  return (
    <div className="bg-gray-700 p-6 rounded-lg shadow-inner">
      <h3 className="text-xl font-semibold text-blue-300">{title}</h3>
      <p className="text-gray-400 mt-1 text-sm">{description}</p>
      <label onDragOver={onDragOver} onDrop={onDrop} className={`mt-4 w-full h-48 border-2 border-dashed border-gray-500 rounded-lg flex flex-col justify-center items-center cursor-pointer hover:border-blue-400 hover:bg-gray-600 transition-colors ${preview ? 'hidden' : 'block'}`}>
        <svg className="w-10 h-10 text-gray-400 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"></path></svg>
        <p className="text-gray-400 text-center">{helpText}</p>
        <input type="file" accept="image/*" onChange={handleFileChange} className="hidden" />
      </label>
      {preview && (
        <div className="mt-4 relative w-full h-48">
            <img src={preview} alt="Preview" className="w-full h-full object-contain rounded-lg" />
            <button onClick={handleRemove} className="absolute top-2 right-2 bg-black bg-opacity-60 text-white rounded-full p-1.5 hover:bg-opacity-80 transition-opacity">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
            </button>
        </div>
      )}
    </div>
  );
};