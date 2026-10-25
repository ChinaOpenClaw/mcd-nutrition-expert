# -*- coding: utf-8 -*-
"""
极客商城系统 · StepAudio 2.5 TTS 引擎（云端 API）
==================================================
通过兼容 OpenAI 规范的 TTS 接口合成中文旁白：
    POST {base}/v1/audio/speech

相比本地 Kokoro 的优势：
  1. **原生多音字控制** —— `pronunciation_map` 参数可直接标注/覆盖指定字符读音，
     这是 Kokoro（需绕 pypinyin 词典层）与 Edge TTS（完全不支持）都做不到的；
  2. **音质与自然度更高** —— 基于语境理解（Global Context + Inline Context），
     支持 `instruction` 指定整段基调、input 内用 () 插入句内情绪指令；
  3. **零本地依赖** —— 不需要 GPU、不需要模型权重。

内置音色
--------
    yuanqishaonv    元气少女
    wenrounvsheng   温柔女声
    wenrougongzi   温柔公子
    ruyananshi     儒雅男士

用法
----
    python tools/tts_stepaudio.py                       # 合成全部旁白（默认音色）
    python tools/tts_stepaudio.py --voice yuanqishaonv
    python tools/tts_stepaudio.py --list-voices
    python tools/tts_stepaudio.py --test "三千多行代码"  # 试听单句
"""
import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
AUDIO_DIR = os.path.join(BASE, 'docs', 'video', 'audio')

API_BASE = os.environ.get('HCNSEC_BASE_URL', 'https://api.hcnsec.cn/v1')
# API Key 必须通过环境变量提供，仓库内不保存任何明文凭据。
#   PowerShell : $env:HCNSEC_API_KEY = "sk-..."
#   bash       : export HCNSEC_API_KEY="sk-..."
API_KEY = os.environ.get('HCNSEC_API_KEY', '')
MODEL = os.environ.get('HCNSEC_TTS_MODEL', 'stepaudio-2.5-tts')

# ============================================================
# 内置音色
# ============================================================
VOICES = {
    'yuanqishaonv': {
        'name': '元气少女',
        'desc': '明亮活泼，适合轻快的技术科普',
    },
    'wenrounvsheng': {
        'name': '温柔女声',
        'desc': '柔和亲切，适合叙事与背景讲解',
    },
    'wenrougongzi': {
        'name': '温柔公子',
        'desc': '温润儒雅，适合情感化叙述',
    },
    'ruyananshi': {
        'name': '儒雅男士',
        'desc': '沉稳专业，**技术讲解首选**',
    },
}
DEFAULT_VOICE = 'ruyananshi'

# ============================================================
# 多音字读音覆盖（StepAudio pronunciation_map 格式）
# ============================================================
# 格式：{词: 拼音}，拼音可带调符号或声调数字
# 覆盖表在 pronunciation_overrides.py 中统一维护（与本地 Kokoro 共用）
from pronunciation_overrides import ALL_OVERRIDES

# 拼音数字调 → 带调符号 的转换表
_TONE_MAP = {
    'a': 'āáǎà', 'e': 'ēéěè', 'i': 'īíǐì', 'o': 'ōóǒò',
    'u': 'ūúǔù', 'ü': 'ǖǘǚǜ', 'n': 'ńňǹ', 'm': 'ḿ',
    'ê': 'ê', 'ü': 'ǖǘǚǜ',
}
_VOWELS = 'aoeiuüê'


def number_tone_to_marked(pinyin):
    """chong2 → chóng；chong → chong（无调号）"""
    if not pinyin:
        return pinyin
    # 末尾声调数字
    if pinyin[-1].isdigit():
        tone = int(pinyin[-1])
        body = pinyin[:-1]
        if tone == 5 or tone == 0:
            return body
        # 找最后一个元音
        for i in range(len(body) - 1, -1, -1):
            if body[i] in _VOWELS:
                marked = _TONE_MAP.get(body[i], body[i])
                if tone <= len(marked):
                    return body[:i] + marked[tone - 1] + body[i + 1:]
                break
        return body
    return pinyin


def build_pronunciation_map(overrides=None):
    """
    生成 pronunciation_map。
    StepAudio 的 tone 字段格式为「字/读音/」，多字词可写「词/音1音2/」。
    这里逐词生成，每词一条，确保定位精确。
    """
    src = overrides if overrides is not None else ALL_OVERRIDES
    items = []
    for word, spec in src.items():
        if not spec:
            continue
        # spec: [[音1], [音2], ...]
        pinyins = [s[0] for s in spec]
        # 优先用带调符号（更明确），否则用数字调
        marked = [number_tone_to_marked(p) for p in pinyins]
        if all(m and any(c in _TONE_MAP.values() for c in m) for m in marked):
            reading = ''.join(marked)
        else:
            reading = ''.join(pinyins)
        items.append({'tone': '%s/%s/' % (word, reading)})
    return items


def synth(text, outfile, voice=DEFAULT_VOICE, pron_map=None,
          instruction=None, response_format='wav', speed=1.0,
          max_retries=3):
    """调用 TTS 接口合成一段文本"""
    if not API_KEY:
        raise RuntimeError(
            '未设置 HCNSEC_API_KEY 环境变量。\n'
            '  PowerShell : $env:HCNSEC_API_KEY = "sk-..."\n'
            '  bash       : export HCNSEC_API_KEY="sk-..."\n'
            '（仓库内不保存明文凭据，请自行申请后注入）')
    payload = {
        'model': MODEL,
        'voice': voice,
        'input': text,
        'response_format': response_format,
    }
    if pron_map:
        payload['pronunciation_map'] = pron_map
    if instruction:
        payload['instruction'] = instruction
    if speed != 1.0:
        payload['speed'] = speed

    url = API_BASE.rstrip('/') + '/audio/speech'
    last_err = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={
                'Authorization': 'Bearer ' + API_KEY,
                'Content-Type': 'application/json',
            },
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            with open(outfile, 'wb') as f:
                f.write(data)
            return len(data)
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'ignore')
            last_err = 'HTTP %d: %s' % (e.code, body[:300])
            if e.code in (400, 401, 403, 404):
                break   # 参数/鉴权问题，重试无意义
        except Exception as e:
            last_err = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError('合成失败：%s' % last_err)


def list_voices():
    print('=' * 68)
    print('StepAudio 2.5 TTS 内置音色')
    print('=' * 68)
    for vid, info in VOICES.items():
        mark = ' ← 默认' if vid == DEFAULT_VOICE else ''
        print('  %-16s %-8s %s%s' % (vid, info['name'], info['desc'], mark))
    print('\n  用法：python tools/tts_stepaudio.py --voice <voice_id>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--voice', default=DEFAULT_VOICE, help='音色 ID')
    ap.add_argument('--speed', type=float, default=1.0, help='语速 0.5-2.0')
    ap.add_argument('--instruction', default=None, help='全局语境指令（整段基调）')
    ap.add_argument('--list-voices', action='store_true', help='列出音色')
    ap.add_argument('--test', default=None, help='试听单句')
    ap.add_argument('--no-pron-map', action='store_true', help='禁用多音字覆盖')
    ap.add_argument('--out-dir', default=None)
    args = ap.parse_args()

    if args.list_voices:
        list_voices()
        return

    if args.voice not in VOICES:
        print('未知音色：%s' % args.voice)
        list_voices()
        sys.exit(1)

    out_dir = args.out_dir or AUDIO_DIR
    os.makedirs(out_dir, exist_ok=True)

    pron_map = None if args.no_pron_map else build_pronunciation_map()

    # 试听模式
    if args.test:
        f = os.path.join(out_dir, '_test_stepaudio.wav')
        size = synth(args.test, f, voice=args.voice, pron_map=pron_map,
                     instruction=args.instruction, speed=args.speed)
        print('已生成：%s（%d bytes）' % (f, size))
        return

    from narration import NARRATION

    print('=' * 68)
    print('StepAudio 2.5 TTS 合成旁白')
    print('=' * 68)
    print('  模型   : %s' % MODEL)
    print('  音色   : %s（%s）' % (args.voice, VOICES[args.voice]['name']))
    print('  多音字 : %d 条覆盖' % (len(pron_map) if pron_map else 0))
    print('  语速   : %.2f' % args.speed)
    if args.instruction:
        print('  语境   : %s' % args.instruction)
    print()

    timeline = []
    total = 0.0
    for i, seg in enumerate(NARRATION, 1):
        wav = os.path.join(out_dir, 's%02d.wav' % i)
        mp3 = os.path.join(out_dir, 's%02d.mp3' % i)
        t0 = time.time()
        try:
            size = synth(seg, wav, voice=args.voice, pron_map=pron_map,
                         instruction=args.instruction, speed=args.speed)
        except Exception as e:
            print('  [FAIL] s%02d  %s' % (i, e))
            continue
        # 转 mp3 减小体积
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            import subprocess
            subprocess.run([ff, '-y', '-loglevel', 'error', '-i', wav, mp3],
                           check=False)
            os.remove(wav)
            final = mp3
        except Exception:
            final = wav
        dur = _wav_duration(final)
        total += dur
        timeline.append({'i': i, 'dur': round(dur, 3),
                         'file': os.path.basename(final)})
        print('  [合成] s%02d  %5.2fs  %4.1fs用时  %s'
              % (i, dur, time.time() - t0, seg[:22]))

    json.dump({'total': round(total, 3), 'segments': timeline,
               'voice': args.voice, 'engine': 'stepaudio-2.5-tts'},
              open(os.path.join(out_dir, 'stepaudio_timeline.json'), 'w',
                   encoding='utf-8'), ensure_ascii=False, indent=2)
    print('\n总时长 %.1fs = %d:%02d' % (total, int(total // 60), int(total % 60)))
    print('输出目录：' + out_dir)


def _wav_duration(path):
    """取音频时长（秒）"""
    import wave
    try:
        with wave.open(path, 'rb') as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        pass
    # mp3 用 ffprobe 估：按 128kbps 估算
    try:
        import imageio_ffmpeg
        import subprocess
        out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-i', path],
                             capture_output=True, text=True,
                             encoding='utf-8', errors='ignore')
        for line in (out.stderr or '').split('\n'):
            if 'Duration:' in line:
                ts = line.split('Duration:')[1].split(',')[0].strip()
                h, m, s = ts.split(':')
                return float(h) * 3600 + float(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


if __name__ == '__main__':
    main()
