# Graphics annotation roadmap

This plan outlines how to add text and shape drawing tools (Korean-friendly fonts, stroke/outline/shadow controls) to the desktop editor while keeping the existing upload → crop → align flow intact.

## Libraries & building blocks
- **PySide6 / Qt**: `QGraphicsView/Scene`, `QGraphicsTextItem`, `QGraphicsPathItem`, `QPainterPath`, `QPen`, `QBrush`, `QColor`, `QFont`, `QFontDatabase` for interactive drawing and styling. Leverage Qt's backing store to keep interactions smooth.
- **Font assets**: Bundle a CJK-friendly font (e.g., Noto Sans KR / Noto Serif KR) and register via `QFontDatabase.addApplicationFont` so Korean text renders reliably across platforms. Allow OS fallback fonts for users who install their own.
- **Effects**: Use `QPainter` layers on export for text outlines and drop shadows (double-pass draw for outline; offset+blur for shadow). Shapes reuse the same stroke/fill parameters.
- **Persistence**: Extend `EditorState` with an `annotations` list containing text items (content, font family/size, fill, outline colour/width, shadow config) and vector items (polyline/polygon points, stroke colour/width, outline/shadow options, fill colour for polygons).
- **Image export**: Keep using Pillow/NumPy for compositing; render annotations into an offscreen `QImage` (matching the overlay size) and convert to NumPy arrays for final blending to preserve outlines/shadows during save.

## Phase breakdown
1. **Toolbar & state scaffolding** *(complete)*
   - Add annotation tool toggles (select/move, text, line, polygon) plus colour pickers for fill/stroke/outline, stroke width slider, outline width slider, shadow toggle/offset/blur slider, and font picker/size field. Default to the bundled Korean-friendly font.
   - Extend `OverlaySettings`/`EditorState` to store active tool, colours, stroke/outline widths, shadow params, and an annotations collection.

2. **Canvas interactions** *(complete)*
   - Introduce annotation layers in `EditorGraphicsView`: create reusable `AnnotationTextItem` and `AnnotationPathItem` classes with draggable anchors and resize/rotate handles for text; multi-point creation for polylines/polygons with ESC/Enter to cancel/finish.
   - Support hit-testing and selection so text and shapes can be re-selected to edit properties (colours, stroke/outline thickness, shadow toggles).
   - Ensure text input respects IME composition for Korean; use `QGraphicsTextItem` with `setTextInteractionFlags` during editing and switch back to item-select mode when confirmed.

3. **Styling and effects**
   - Implement outline rendering by drawing the stroke multiple times expanded by outline width; add drop shadow via blur kernel (QGraphicsDropShadowEffect for live preview, manual painter pass for export).
   - Allow separate stroke vs outline colours for lines/polylines; polygons can optionally fill with a semi-transparent colour while keeping the outline visible.

4. **Persistence & export**
   - Serialize annotations into `EditorState` (positions in overlay pixel space) so they survive navigation resets and can be reloaded with the aligned overlay.
   - During save, render the current overlay (with line thickness/outline/smoothing) and composite the annotation layer QImage before blending onto the photo. Respect EXIF handling as today.

5. **UX polish & performance**
   - Add undo/redo stack for annotation edits, snap-to-angle toggles for polylines, and keyboard shortcuts (Delete to remove, Ctrl/Cmd+Z/Y for undo/redo).
   - Profile large text/shadow renders; if needed, cache glyph paths or rasterized text per font/size to speed up redraws while dragging.

## Testing checklist
- Korean IME text entry works for both placeholder and committed text in the graphics view.
- Outline/shadow colours survive export and match on-screen preview.
- Annotations remain editable after switching tools or saving/reloading the overlay alignment.
- Performance remains interactive with multiple text boxes and polygons on large overlays.
