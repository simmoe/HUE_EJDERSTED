export type AirStatus = {
  online?: boolean;
  pm25?: number | null;
  tempC?: number | null;
  humidity?: number | null;
  voc?: number | null;
};

export function formatAirLine(air: AirStatus | null | undefined): string {
  if (!air?.online) return '';
  const band = airQualityBand(air.pm25 ?? null);
  return band ? `luft: ${band}` : '';
}

export type AirBand = 'god' | 'middel' | 'dårlig';

export function airQualityBand(pm25: number | null | undefined): AirBand | '' {
  if (pm25 == null || Number.isNaN(pm25)) return '';
  if (pm25 <= 35) return 'god';
  if (pm25 <= 120) return 'middel';
  return 'dårlig';
}

export function airQualityLabel(pm25: number | null | undefined): string {
  return airQualityBand(pm25);
}
