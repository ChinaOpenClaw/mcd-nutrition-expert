# -*- coding: utf-8 -*-
"""
极客商城系统 · 旁白音轨装配
================================
把各段旁白音频拼接为单轨 wav，并输出时间表 timeline.json，
供 build_video.py 按段对齐幻灯片。

支持两种音源：
  · Kokoro（本地离线，推荐）：docs/video/audio/k*.wav
  · Edge TTS（在线，云端）：docs/video/audio/n*.wav

用法：
    python tools/assemble_narration.py --source kokoro
    python tools/assemble_narration.py --source edge
"""
import os
import sys
import json
import wave
import argparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUD = os.path.join(BASE, 'docs', 'video', 'audio')

LEAD, TAIL = 0.55, 0.95
SAMPLE_RATE = 44100


def find_ffmpeg():
    """
    定位 ffmpeg。优先级：环境变量 → imageio_ffmpeg → @ffmpeg-installer → PATH。
    不强制依赖 imageio_ffmpeg（该包在本机不一定存在）。
    """
    import os as _os
    import glob

    env = _os.environ.get('MCD_FFMPEG')
    if env and _os.path.isfile(env):
        return env
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    pats = [
        r'E:\dxsj_work\tools\node_modules\@ffmpeg-installer\win32-x64\ffmpeg.exe',
        _os.path.join(_os.path.dirname(_os.path.dirname(
            _os.path.abspath(__file__))), 'node_modules',
            '@ffmpeg-installer', 'win32-x64', 'ffmpeg.exe'),
    ]
    for p in pats:
        if _os.path.isfile(p):
            return p
    for base in ('C:/ffmpeg/bin', 'C:/Program Files/ffmpeg/bin'):
        for exe in glob.glob(base + '/ffmpeg.exe'):
            return exe
    return 'ffmpeg'


def to_wav(src, dst):
    """任意音频 → 44.1k 单声道 wav"""
    import subprocess
    ff = find_ffmpeg()
    subprocess.run([ff, '-y', '-loglevel', 'error', '-i', src,
                    '-ar', str(SAMPLE_RATE), '-ac', '1', dst], check=True)


def collect(source):
    """收集各段音频文件路径"""
    # 各音源：文件名前缀 + 扩展名
    #   kokoro   → k01.wav   （本地合成，保留 wav）
    #   stepaudio→ s01.mp3   （云端合成，转 mp3 压缩）
    #   edge     → n01.wav   （旧方案，保留 wav）
    #
    # ⚠️ stepaudio 优先取 mp3（体积小），但若 mp3 转换失败（缺 imageio_ffmpeg）
    #    会回退到同名 wav —— 两种扩展名都要支持，不能因为 mp3 不在就判「未找到」。
    cfg = {
        'kokoro':   ('k', 'wav'),
        'stepaudio': ('s', 'mp3'),
        'edge':     ('n', 'wav'),
    }
    prefix, ext = cfg.get(source, ('k', 'wav'))
    files = []
    i = 1
    while True:
        p = os.path.join(AUD, (prefix + '%02d.' % i) + ext)
        if not os.path.isfile(p):
            # 回退：同序号的其他扩展名
            alt = None
            for e2 in ('wav', 'mp3', 'm4a', 'opus'):
                p2 = os.path.join(AUD, (prefix + '%02d.' % i) + e2)
                if os.path.isfile(p2):
                    alt = p2
                    break
            if not alt:
                break
            p = alt
        files.append((i, p))
        i += 1
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', default='kokoro',
                    choices=['kokoro', 'stepaudio', 'edge'])
    args = ap.parse_args()

    files = collect(args.source)
    if not files:
        print('未找到 %s 音轨（先运行对应的 tts 脚本）' % args.source)
        sys.exit(1)

    print('装配 %s 音轨：%d 段' % (args.source, len(files)))

    timeline = []
    total = 0.0
    out = os.path.join(AUD, 'narration.wav')

    with wave.open(out, 'wb') as o:
        o.setnchannels(1)
        o.setsampwidth(2)
        o.setframerate(SAMPLE_RATE)

        for idx, path in files:
            wav = os.path.join(AUD, 'tmp_%02d.wav' % idx)
            to_wav(path, wav)

            with wave.open(wav, 'rb') as r:
                sw, sr = r.getsampwidth(), r.getframerate()
                if sw != 2:
                    raise RuntimeError('采样位宽异常: %d' % sw)
                data = r.readframes(r.getnframes())
                d = len(data) / 2.0 / SAMPLE_RATE

            lead = b'\x00' * int(LEAD * SAMPLE_RATE) * 2
            tail = b'\x00' * int(TAIL * SAMPLE_RATE) * 2
            o.writeframes(lead)
            o.writeframes(data)
            o.writeframes(tail)

            seg = LEAD + d + TAIL
            timeline.append({'i': idx, 'start': round(total, 3),
                             'dur': round(seg, 3), 'speech': round(d, 2)})
            total += seg
            os.remove(wav)
            print('  段 %02d  起始 %6.1fs  时长 %5.1fs' % (idx, total - seg, seg))

    json.dump({'total': round(total, 3), 'segments': timeline, 'source': args.source},
              open(os.path.join(AUD, 'timeline.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)

    print('\n总时长 %.1fs = %d:%02d' % (total, int(total // 60), int(total % 60)))
    print('音轨：' + out)


if __name__ == '__main__':
    main()
