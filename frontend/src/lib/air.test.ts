import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { airQualityBand, airQualityLabel, formatAirLine, indoorHumidityHigh } from './air.ts';

describe('formatAirLine', () => {
  it('says the IKEA band, not the raw numbers', () => {
    assert.equal(
      formatAirLine({ online: true, pm25: 8.2, tempC: 19.4, humidity: 44.1 }),
      'luft: god',
    );
  });

  it('is empty when the sensor is offline', () => {
    assert.equal(formatAirLine({ online: false, pm25: 8 }), '');
  });

  it('flags indoor humidity at the 75 mark', () => {
    assert.equal(
      formatAirLine({ online: true, pm25: 8, humidity: 75 }),
      'luft: god · fugt',
    );
    assert.equal(
      formatAirLine({ online: true, pm25: null, humidity: 81 }),
      'luft: fugt',
    );
  });
});

describe('indoorHumidityHigh', () => {
  it('starts at 75', () => {
    assert.equal(indoorHumidityHigh(74.9), false);
    assert.equal(indoorHumidityHigh(75), true);
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
