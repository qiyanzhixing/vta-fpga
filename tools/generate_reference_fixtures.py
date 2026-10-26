"""Offline oracle. Imports the unchanged reference module; never invokes main/plots."""
import os
os.environ['MPLBACKEND']='Agg'
os.environ.setdefault('MPLCONFIGDIR','/private/tmp/vta-matplotlib')
import argparse,hashlib,importlib.util,json,logging,pathlib,platform,sys
import numpy as np
import pandas as pd
import scipy,pywt,matplotlib
logging.disable(logging.CRITICAL)
def generate(source):
 source=pathlib.Path(source).resolve()
 spec=importlib.util.spec_from_file_location('denoise_reference',source)
 ref=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ref;spec.loader.exec_module(ref)
 rng=np.random.default_rng(20261026);cases=[]
 def add(name,values,method,params):
  values=np.asarray(values,dtype=float)
  if values.ndim==1:values=values[:,None]
  n,c=values.shape;channels=[f'mz{i+5}' for i in range(c)]
  expected=values.copy();codes=[];calibrations={}
  if method=='mz5_11':
   manager=ref.DenoiseConfigManager(default_method='none')
   for ch in channels:manager.set_column_config(ch,method,params.copy())
   processor=ref.SelectiveDenoiser(manager);df=pd.DataFrame(values,columns=channels)
   prepared=processor._prepare_mz5_11_params(df,channels)
   for j,ch in enumerate(channels):
    if ch in params['no_gas_mz']:codes.append('REFERENCE_CHANNEL')
    elif ch in prepared:
     expected[:,j]=ref.MzBaselineNoiseDenoise().denoise(values[:,j],**prepared[ch]);codes.append('PROCESSED')
     calibrations[ch]={k:prepared[ch][k] for k in ('lag','a','c')}
    else:codes.append('CALIBRATION_LOW_VARIATION' if 'constant' in name else 'CALIBRATION_SCALE_OUT_OF_RANGE')
  else:
   for j in range(c):
    expected[:,j]=ref.DenoiseMethod.create(method).denoise(values[:,j],**params)
    code='NONE' if method=='none' else 'PROCESSED'
    if method=='savitzky_golay' and n<params.get('window_size',11) or method=='wavelet' and n<8:code='FALLBACK_SHORT_SERIES'
    elif method=='wavelet':
     maxlevel=pywt.dwt_max_level(n,8);actual=max(1,min(params.get('level',3),maxlevel)) if maxlevel>=1 else 1
     if actual!=params.get('level',3):code='WAVELET_LEVEL_ADJUSTED'
    if not np.all(np.isfinite(expected[:,j])):expected[:,j]=values[:,j];code='NON_FINITE_RESULT'
    codes.append(code)
  cases.append(dict(name=name,data=dict(time_unit='min',time=(np.arange(n)*.1).tolist(),channels=channels,intensity=values.tolist()),config=dict(method=method,params=params),expected_intensity=expected.tolist(),expected_column_codes=codes,calibrations=calibrations,rtol=1e-7,atol=1e-24))
 methods='none mean std_threshold ewma median quantile savitzky_golay wavelet kalman'.split()
 for n in [1,7,8,64,65]:
  x=rng.normal(size=(n,2))*1e-12
  for method in methods:add(f'{method}_{n}',x,method,{})
 for method in methods:
  add(f'{method}_constant',np.full(65,2e-12),method,{})
  add(f'{method}_zero',np.zeros(8),method,{})
 for method in ['mean','median','quantile']:add(f'{method}_even',rng.normal(size=64),method,{'window_size':4})
 for mode in ['soft','hard']:add(f'wavelet_{mode}',rng.normal(size=65)*1e-12,'wavelet',{'threshold_type':mode,'level':2})
 rank_input=np.arange(181,dtype=float)*1e-12
 rank_input[90],rank_input[107]=rank_input[107],rank_input[90]
 add('quantile_rank_rounding',rank_input,'quantile',{'window_size':90,'quantile':.7})
 x=(2+rng.normal(size=65))*1e-12
 for name,y in [('success',1.2*x+1e-13),('scale',5*x),('constant',np.ones(65)*1e-12),('shift',1.1*ref._shift_signal(x,2))]:
  add('baseline_'+name,np.column_stack([x,y]),'mz5_11',{'no_gas_mz':['mz5'],'T_idx':[0,60],'k':5,'max_lag':4})
 deps={m.__name__:m.__version__ for m in [np,pd,scipy,pywt,matplotlib]}
 return dict(reference_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),python_version=platform.python_version(),dependencies=deps,cases=cases)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--source',default='docs/deNoise_ms.py');p.add_argument('--output',default='internal/denoise/testdata/reference.json');a=p.parse_args();doc=generate(a.source);out=pathlib.Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(doc,allow_nan=False,indent=2)+'\n');print(f'Generated {len(doc["cases"])} cases')
