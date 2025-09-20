import React, { useEffect, useRef } from 'react';
import type { Point } from '../types';

interface DraggablePointProps {
  point: Point;
  onDrag: (point: Point) => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  containerRect: DOMRect | null;
  className?: string;
  children?: React.ReactNode;
}

export const DraggablePoint: React.FC<DraggablePointProps> = ({ point, onDrag, onDragStart, onDragEnd, containerRect, className, children }) => {
  const isDraggingRef = useRef(false);

  // Use refs for props and callbacks to ensure the listeners in the useEffect hook
  // always have access to the latest values without needing to be re-attached.
  const onDragRef = useRef(onDrag);
  useEffect(() => { onDragRef.current = onDrag; }, [onDrag]);

  const onDragEndRef = useRef(onDragEnd);
  useEffect(() => { onDragEndRef.current = onDragEnd; }, [onDragEnd]);
  
  const containerRectRef = useRef(containerRect);
  useEffect(() => { containerRectRef.current = containerRect; }, [containerRect]);

  // This single useEffect handles all global event listener logic.
  // It runs only once on mount and cleans up on unmount.
  useEffect(() => {
    const handleMouseMove = (moveEvent: MouseEvent) => {
      // Only execute logic if dragging is active.
      if (!isDraggingRef.current) return;
      
      const rect = containerRectRef.current;
      if (!rect) return;

      onDragRef.current({
        x: moveEvent.clientX - rect.left,
        y: moveEvent.clientY - rect.top
      });
    };

    const handleMouseUp = () => {
      // Only execute logic if dragging was active.
      if (!isDraggingRef.current) return;
      
      isDraggingRef.current = false;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      onDragEndRef.current();
    };

    // Attach listeners to the window.
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    // CRITICAL: Cleanup function runs when the component unmounts.
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);

      // If the component unmounts while a drag is in progress (e.g., mode switch),
      // we must manually reset the body styles to prevent the UI from getting stuck.
      if (isDraggingRef.current) {
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
      }
    };
  }, []); // Empty dependency array ensures this effect runs only on mount and unmount.

  const handleMouseDown = (e: React.MouseEvent) => {
    e.stopPropagation();
    isDraggingRef.current = true;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'grabbing';
    onDragStart();
  };

  const defaultClassName = "absolute w-5 h-5 -translate-x-1/2 -translate-y-1/2 bg-blue-500 border-2 border-white rounded-full cursor-grab active:cursor-grabbing shadow-lg";

  return (
    <div
      onMouseDown={handleMouseDown}
      className={className || defaultClassName}
      style={{ left: `${point.x}px`, top: `${point.y}px` }}
    >
      {children}
    </div>
  );
};
