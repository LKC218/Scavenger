import assert from 'node:assert/strict';
import {CameraScaleTransition} from '../../src/战术演训/镜头过渡.mjs';
const a=new CameraScaleTransition(320);a.start(100,10000,1000);
assert.equal(a.sample(1000),100);assert.ok(Math.abs(a.sample(1160)-1000)<1e-8);assert.equal(a.sample(1320),10000);assert.equal(a.active,false);
a.start(10000,100,2000);const values=[2000,2080,2160,2240,2320].map(t=>a.sample(t));assert.ok(values.every((v,i)=>!i||v<=values[i-1]));
a.start(100,10000,3000);const interrupted=a.sample(3080);a.cancel(interrupted);assert.equal(a.sample(9000),interrupted);a.start(interrupted,80,9000);assert.equal(a.sample(9000),interrupted);assert.equal(a.sample(9320),80);
assert.throws(()=>a.start(0,50,0));assert.throws(()=>a.start(50,NaN,0));
console.log('镜头过渡规则通过：对数中点、终点、反向、接管、重定向、无效值。');
