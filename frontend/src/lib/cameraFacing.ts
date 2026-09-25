export type CameraFacing = 'user' | 'environment';

export const CAMERA_FACING_DOC = 'camera_garden';

export function parseFacing(value: unknown): CameraFacing | '' {
  return value === 'user' || value === 'environment' ? value : '';
}
