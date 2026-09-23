import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { airQualityBand, airQualityLabel, formatAirLine } from './air.ts';

describe('formatAirLine', () => {
  it('joins the three readings like the camera battery line', () => {
    assert.equal(
      formatAirLine({ online: true, pm25: 8.2, tempC: 19.4, humidity: 44.1 }),
      'luft 8 µg · 19° · 44 %',
    );
  });

  it('is empty when the sensor is offline', () => {
    assert.equal(formatAirLine({ online: false, pm25: 8 }), '');
  });
});

describe('airQualityLabel', () => {
  it('uses the IKEA traffic-light bands', () => {
    assert.equal(airQualityLabel(12), 'god');
    assert.equal(airQualityLabel(40), 'middel');
    assert.equal(airQualityLabel(140), 'dårlig');
  });
});

describe('airQualityBand', () => {
  it('is empty without a reading', () => {
    assert.equal(airQualityBand(null), '');
  });
});
