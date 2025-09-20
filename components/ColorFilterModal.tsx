import React, { useState, useId, useRef, useEffect } from 'react';
import type { ColorFilter } from '../types';

interface ColorFilterModalProps {
    initialFilters: { keep: ColorFilter[]; remove: ColorFilter[] };
    onApply: (newFilters: { keep: ColorFilter[]; remove: ColorFilter[] }) => void;
    onClose: () => void;
}

const defaultNewColor: ColorFilter = { color: '#000000', tolerance: 50 };

const FilterRow: React.FC<{
    filter: ColorFilter,
    onUpdate: (updatedFilter: ColorFilter) => void,
    onRemove: () => void
}> = ({ filter, onUpdate, onRemove }) => {
    const id = useId();
    return (
        <div className="p-2 bg-gray-700 rounded-lg flex items-center space-x-3">
            <input 
                type="color" 
                value={filter.color}
                onChange={(e) => onUpdate({ ...filter, color: e.target.value })}
                className="w-10 h-10 p-1 bg-gray-600 border border-gray-500 rounded-md cursor-pointer"
                aria-label="Select color"
            />
            <div className="flex-grow flex flex-col space-y-2">
                <div className="flex items-center space-x-2">
                    <label htmlFor={`hex-${id}`} className="text-sm font-mono text-gray-400">HEX</label>
                    <input
                        id={`hex-${id}`}
                        type="text"
                        value={filter.color}
                        onChange={(e) => onUpdate({ ...filter, color: e.target.value })}
                        className="w-24 bg-gray-800 text-gray-200 border border-gray-600 rounded px-2 py-1 text-sm font-mono focus:ring-2 focus:ring-purple-500 focus:border-purple-500 outline-none"
                    />
                </div>
                <div className="flex items-center space-x-2">
                    <label htmlFor={`tolerance-${id}`} className="text-sm font-medium text-gray-300 w-16">Tolerance</label>
                    <input
                        id={`tolerance-${id}`}
                        type="range"
                        min="0"
                        max="100"
                        value={filter.tolerance}
                        onChange={(e) => onUpdate({ ...filter, tolerance: parseInt(e.target.value, 10) })}
                        className="w-full cursor-pointer accent-purple-500"
                    />
                    <span className="text-sm font-medium text-gray-300 w-8 text-right">{filter.tolerance}</span>
                </div>
            </div>
            <button onClick={onRemove} className="p-2 rounded-full bg-gray-600 hover:bg-red-500 text-gray-300 hover:text-white transition-colors">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
            </button>
        </div>
    );
};

export const ColorFilterModal: React.FC<ColorFilterModalProps> = ({ initialFilters, onApply, onClose }) => {
    const [filters, setFilters] = useState(initialFilters);
    const modalRef = useRef<HTMLDivElement>(null);
    const [position, setPosition] = useState<{ x: number, y: number } | null>(null);
    const isDraggingRef = useRef(false);
    const dragOffsetRef = useRef({ x: 0, y: 0 });

    const handleUpdate = (type: 'keep' | 'remove', index: number, updatedFilter: ColorFilter) => {
        const newFilters = { ...filters };
        newFilters[type][index] = updatedFilter;
        setFilters(newFilters);
    };

    const handleRemove = (type: 'keep' | 'remove', index: number) => {
        const newFilters = { ...filters };
        newFilters[type].splice(index, 1);
        setFilters(newFilters);
    };
    
    const handleAdd = (type: 'keep' | 'remove') => {
        const newFilters = { ...filters };
        newFilters[type].push({ ...defaultNewColor });
        setFilters(newFilters);
    };

    const handleMouseDown = (e: React.MouseEvent<HTMLElement>) => {
        if (!modalRef.current) return;

        isDraggingRef.current = true;
        document.body.style.userSelect = 'none';
        
        const modalRect = modalRef.current.getBoundingClientRect();

        // On first drag, capture current position from DOM and switch to controlled positioning
        if (position === null) {
            setPosition({ x: modalRect.left, y: modalRect.top });
        }

        dragOffsetRef.current = {
            x: e.clientX - modalRect.left,
            y: e.clientY - modalRect.top
        };
    };

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isDraggingRef.current) return;
            setPosition({
                x: e.clientX - dragOffsetRef.current.x,
                y: e.clientY - dragOffsetRef.current.y
            });
        };

        const handleMouseUp = () => {
            if (!isDraggingRef.current) return;
            isDraggingRef.current = false;
            document.body.style.userSelect = '';
        };

        window.addEventListener('mousemove', handleMouseMove);
        window.addEventListener('mouseup', handleMouseUp);
        
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
            window.removeEventListener('mouseup', handleMouseUp);
            // If the component unmounts while a drag is in progress, clean up body styles
            if (isDraggingRef.current) {
                document.body.style.userSelect = '';
            }
        };
    }, []); // Empty dependency array ensures this effect runs only on mount and unmount.


    // The main container is now the modal itself, not a backdrop.
    // It's positioned with 'fixed' and its location is controlled by state.
    // z-50 keeps it on top of other content.
    return (
        <div 
            ref={modalRef}
            className="fixed bg-gray-800 text-gray-200 rounded-xl shadow-2xl w-full max-w-md max-h-[90vh] flex flex-col border border-gray-700 z-50"
            style={position ? {
                top: `${position.y}px`,
                left: `${position.x}px`,
                transform: 'none',
            } : {
                top: '50%',
                left: '50%',
                transform: 'translate(-50%, -50%)',
            }}
        >
            <header 
                className="p-4 border-b border-gray-700 flex justify-between items-center cursor-move"
                onMouseDown={handleMouseDown}
            >
                <h2 className="text-xl font-bold text-purple-300">Advanced Color Filters</h2>
                <button onClick={onClose} className="p-1 rounded-full hover:bg-gray-700 cursor-pointer">
                     <svg xmlns="http://www.w3.org/2000/svg" className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
            </header>
            <main className="p-6 overflow-y-auto flex-grow grid grid-cols-1 gap-6">
                {/* Colors to Keep */}
                <div className="flex flex-col space-y-3">
                     <h3 className="text-lg font-semibold text-green-400">Colors to Keep</h3>
                     <div className="space-y-3 pr-2 overflow-y-auto max-h-[45vh]">
                        {filters.keep.map((f, i) => (
                            <FilterRow key={`keep-${i}`} filter={f} onUpdate={(uf) => handleUpdate('keep', i, uf)} onRemove={() => handleRemove('keep', i)} />
                        ))}
                     </div>
                     <button onClick={() => handleAdd('keep')} className="w-full py-2 bg-green-600 hover:bg-green-700 rounded-lg text-sm font-semibold transition-colors">+ Add Color to Keep</button>
                </div>
                 {/* Colors to Remove */}
                <div className="flex flex-col space-y-3">
                     <h3 className="text-lg font-semibold text-red-400">Colors to Remove</h3>
                     <div className="space-y-3 pr-2 overflow-y-auto max-h-[45vh]">
                        {filters.remove.map((f, i) => (
                            <FilterRow key={`remove-${i}`} filter={f} onUpdate={(uf) => handleUpdate('remove', i, uf)} onRemove={() => handleRemove('remove', i)} />
                        ))}
                     </div>
                     <button onClick={() => handleAdd('remove')} className="w-full py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-semibold transition-colors">+ Add Color to Remove</button>
                </div>
            </main>
            <footer className="p-4 border-t border-gray-700 flex justify-end items-center space-x-4">
                <button onClick={onClose} className="px-6 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 font-semibold transition-colors">Cancel</button>
                <button onClick={() => onApply(filters)} className="px-8 py-2 rounded-lg bg-purple-600 hover:bg-purple-700 font-semibold text-white transition-colors shadow-md">Apply Filters</button>
            </footer>
        </div>
    );
};
