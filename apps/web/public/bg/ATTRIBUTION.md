# Waves background attribution

`waves-dark.png` is a derived pixel-art rendering (320x176, three colours: transparent,
`#0f8b8d`, `#ffffff`) of:

- Work: Katsushika Hokusai, *Under the Wave off Kanagawa (The Great Wave)*.
- Licence: public domain (Metropolitan Museum of Art Open Access, object 45434).
- Source file: `https://upload.wikimedia.org/wikipedia/commons/a/a5/Tsunami_by_hokusai_19th_century.jpg`
  (3859x2594), sha256 `30c170260de393f51bd592adacc6f0714b60f157c09744e9c5b6d46e6a613718`.
  The source image is not committed.

Regenerate (deterministic, byte-identical output):

```sh
uv run --with pillow --with numpy python scripts/assets/pixelate_background.py \
  --source <great-wave.jpg> --out apps/web/public/bg/waves-dark.png
```
