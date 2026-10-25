# -*- coding: utf-8 -*-
"""只重合成指定段落的旁白（不重复消耗其余段的配额）。

用法：
    HCNSEC_API_KEY=sk-xxx python tools/resynth.py 3 4
"""
import os
import sys
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tts_stepaudio as T
from narration import NARRATION, INSTRUCTION
from pronunciation_overrides import ALL_OVERRIDES

TARGETS = [int(x) for x in sys.argv[1:]] or [3]
AUD = T.AUDIO_DIR

FF = None
try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    for p in [r'E:\dxsj_work\tools\node_modules\@ffmpeg-installer\win32-x64\ffmpeg.exe']:
        if os.path.isfile(p):
            FF = p
            break

pron = T.build_pronunciation_map(ALL_OVERRIDES)
print('pron_map 条数 =', len(pron) if pron else 0)

for i in TARGETS:
    seg = NARRATION[i - 1]
    wav = os.path.join(AUD, 's%02d.wav' % i)
    size = T.synth(seg, wav, voice='ruyananshi', pron_map=pron,
                   instruction=INSTRUCTION, speed=1.2)
    dur = T._wav_duration(wav)
    mp3 = os.path.join(AUD, 's%02d.mp3' % i)
    if FF:
        subprocess.run([FF, '-y', '-loglevel', 'error', '-i', wav, mp3], check=False)
        if os.path.isfile(mp3):
            os.remove(wav)
        else:
            print('  [WARN] mp3 转换失败，保留 wav')
    print('段 %02d 重合成完成  %d bytes  %.1fs  (%d 字)' % (i, size, dur, len(seg)))
