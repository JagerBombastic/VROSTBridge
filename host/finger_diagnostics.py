"""Validate read-only evidence emitted by the matching native controller driver."""
import json
import math


def native_hand_sample(content):
    if not content.startswith('VROST_NATIVE_HAND ') or len(content)>2048:return None
    try:
        sample=json.loads(content[len('VROST_NATIVE_HAND '):])
        if not isinstance(sample,dict) or sample.get('hand') not in ('left','right') or sample.get('ranges')!='both':return None
        for name in ('source_curls_valid','applied'):
            if type(sample.get(name)) is not bool:return None
        for name,count in (('source_curls',5),('applied_curls',4)):
            values=sample.get(name)
            if not isinstance(values,list) or len(values)!=count or not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in values):return None
        for name in ('sample_ns','packet_ns'):
            value=sample.get(name)
            if not isinstance(value,str) or not value.isascii() or not value.isdecimal() or len(value)>20:return None
        age=sample.get('sample_age_ms')
        if type(age) not in (int,float) or not math.isfinite(age) or age<0:return None
        updates=sample.get('updates')
        if type(updates) is not int or updates<0:return None
        return sample['hand'],sample
    except (ValueError,TypeError):return None
