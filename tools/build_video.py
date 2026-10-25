# -*- coding: utf-8 -*-
"""
雨夜麦当劳 · 讲解视频合成
================================
将 14 页幻灯片静帧 + 女声旁白音轨合成为 1080p 讲解视频。

设计要点（沿用前几个项目的经验）：
  1. 画面完全静止，不做推镜/缩放 —— 避免逐帧亚像素采样造成的抖动；
  2. 仅做 0.30s 淡入 / 0.34s 淡出，硬切转场，视觉干净；
  3. 音画严格按 timeline.json 对齐，保证旁白与画面同步。
"""
import os, json, subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VID = os.path.join(BASE, 'docs', 'video')
SLIDES = os.path.join(VID, 'slides')
AUD = os.path.join(VID, 'audio')
TMP = os.path.join(VID, 'tmp')
FPS = 25


def find_ffmpeg():
    """
    定位 ffmpeg 可执行文件。

    ⚠️ 不要直接 `import imageio_ffmpeg` —— 该包在本机不一定装了，
    装了也不保证在所有环境可复现。这里按优先级逐个探测：
      1. 环境变量 MCD_FFMPEG
      2. imageio_ffmpeg（若可用）
      3. @ffmpeg-installer 的 win32-x64 目录
      4. PATH 上的 ffmpeg
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
    return 'ffmpeg'   # 交给 PATH 解析


FF = find_ffmpeg()


def run(args):
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding='utf-8', errors='ignore')
    if p.returncode != 0:
        print('FFMPEG FAILED:', ' '.join(args[:14]))
        print(p.stderr[-2000:])
        raise SystemExit(1)


def main():
    tl_path = os.path.join(AUD, 'timeline.json')
    if not os.path.exists(tl_path):
        print('未找到 timeline.json，请先运行 assemble_narration.py')
        raise SystemExit(1)
    tl = json.load(open(tl_path, encoding='utf-8'))
    os.makedirs(TMP, exist_ok=True)
    segs = []

    for s in tl['segments']:
        i, dur = s['i'], s['dur']
        out = os.path.join(TMP, 'seg-%02d.mp4' % i)
        img = os.path.join(SLIDES, 'slide-%02d.png' % i)
        fade_out_st = max(0.1, dur - 0.36)
        vf = ("fps=%d,scale=1920:1080:flags=lanczos,"
              "fade=t=in:st=0:d=0.30,fade=t=out:st=%.3f:d=0.34,"
              "format=yuv420p" % (FPS, fade_out_st))
        run([FF, '-y', '-loglevel', 'error', '-loop', '1', '-i', img,
             '-t', '%.3f' % dur, '-vf', vf, '-r', str(FPS),
             '-c:v', 'libx264', '-crf', '21', '-preset', 'slow', '-g', '50', out])
        print('segment %02d  %.2fs  [静帧]' % (i, dur))
        segs.append(out)

    # 拼接
    lst = os.path.join(TMP, 'list.txt')
    with open(lst, 'w', encoding='utf-8') as f:
        for p in segs:
            f.write("file '%s'\n" % p.replace('\\', '/'))

    silent = os.path.join(TMP, 'video.mp4')
    run([FF, '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
         '-i', lst, '-c', 'copy', silent])

    # 混流（女声旁白）
    final = os.path.join(VID, '麦当劳营养膳食专家_项目讲解_1080p.mp4')
    run([FF, '-y', '-loglevel', 'error',
         '-i', silent, '-i', os.path.join(AUD, 'narration.wav'),
         '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '44100',
         '-shortest', '-movflags', '+faststart', final])

    print('\n输出：%s  (%.1f MB)' % (final, os.path.getsize(final) / 1048576))
    print('时长：%.1f s = %d:%02d' % (tl['total'], int(tl['total'] // 60),
                                     int(tl['total'] % 60)))


if __name__ == '__main__':
    main()
