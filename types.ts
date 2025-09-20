export enum AppStateEnum {
  UPLOADING = 'UPLOADING',
  CROPPING = 'CROPPING',
  EDITING = 'EDITING',
}
export type AppState = AppStateEnum;

export interface Point {
  x: number;
  y: number;
}

export interface ColorFilter {
  color: string;
  tolerance: number;
}
