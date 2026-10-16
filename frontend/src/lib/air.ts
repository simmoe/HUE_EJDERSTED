export type AirStatus = {
  online?: boolean;
  pm25?: number | null;
  tempC?: number | null;
  humidity?: number | null;
  outdoorHumidity?: number | null;
  outdoorSource?: string;
  voc?: number | null;
};

export const INDOOR_HUMIDITY_WARN = 75;

export function indoorHumidityHigh(humidity: number | null | undefined): boolean {
  return humidity != null && !Number.isNaN(humidity) && humidity >= INDOOR_HUMIDITY_WARN;
}

export function formatAirLine(air: AirStatus | null | undefined): string {
  if (!air?.online) return '';
  const band = airQualityBand(air.pm25 ?? null);
  const wet = indoorHumidityHigh(air.humidity);
  if (band && wet) return `luft: ${band} · fugt`;
  if (wet) return 'luft: fugt';
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
