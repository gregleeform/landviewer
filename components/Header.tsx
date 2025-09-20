import React from 'react';

interface HeaderProps {
    onReset?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ onReset }) => (
  <header className="w-full max-w-7xl mx-auto flex justify-between items-center p-4 mb-6 border-b border-gray-700">
    <div className="flex items-center space-x-3">
      {/* FIX: Replaced malformed SVG with a correct version of the map icon. The original contained invalid characters and structure inside the path's d attribute, causing cascading parsing errors. */}
      <svg className="w-10 h-10 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13V7m6 10l5.447 2.724A1 1 0 0021 16.382V5.618a1 1 0 00-1.447-.894L15 7m-6 3l6-3" />
      </svg>
      <h1 className="text-2xl sm:text-3xl font-bold text-white tracking-tight">
        Land<span className="text-blue-400">Viewer</span>
      </h1>
    </div>
    {onReset && (
        <button
            onClick={onReset}
            className="bg-gray-700 text-gray-300 font-semibold py-2 px-4 rounded-lg shadow-md hover:bg-gray-600 transition-colors duration-300 flex items-center space-x-2"
        >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.898 2.186l-1.432.716a5.002 5.002 0 00-8.464-1.928V7a1 1 0 01-2 0V3a1 1 0 011-1zm12 16a1 1 0 01-1-1v-2.101a7.002 7.002 0 01-11.898-2.186l1.432-.716a5.002 5.002 0 008.464 1.928V13a1 1 0 012 0v5a1 1 0 01-1 1z" clipRule="evenodd" />
            </svg>
            <span>Start Over</span>
        </button>
    )}
  </header>
);