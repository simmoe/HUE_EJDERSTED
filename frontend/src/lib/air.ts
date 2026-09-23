export type AirStatus = {
  online?: boolean;
  pm25?: number | null;
  tempC?: number | null;
  humidity?: number | null;
  voc?: number | null;
};

export function formatAirLine(air: AirStatus | null | undefined): string {
  if (!air?.online) return '';
  const parts: string[] = [];
  if (typeof air.pm25 === 'number') parts.push(`${Math.round(air.pm25)} µg`);
  if (typeof air.tempC === 'number') parts.push(`${Math.round(air.tempC)}°`);
  if (typeof air.humidity === 'number') parts.push(`${Math.round(air.humidity)} %`);
  if (!parts.length) return '';
  return `luft ${parts.join(' · ')}`;
}

export function airQualityLabel(pm25: number | null | undefined): string {
  if (pm25 == null || Number.isNaN(pm25)) return '';
  if (pm25 <= 35) return 'god';
  if (pm25 <= 120) return 'middel';
  return 'dårlig';
}
