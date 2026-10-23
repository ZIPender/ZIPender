#!/usr/bin/env bash
# Write .preview/dark.html and .preview/light.html (git-ignored): the README laid out the way GitHub shows it,
# in both themes. Open one in a browser to see the animations play.
#   tools/preview.sh          # write the pages
#   tools/preview.sh --open   # and open the dark one
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .preview
body=$(grep -v '^<!--' README.md)
for theme in dark light; do
  if [[ $theme == dark ]]; then bg='#0d1117' fg='#e6edf3' border='#30363d' link='#4493f8'
  else bg='#ffffff' fg='#1f2328' border='#d1d9e0' link='#0969da'; fi
  cat > ".preview/$theme.html" <<EOF
<!doctype html><meta charset="utf-8"><title>profile preview ($theme)</title><base href="../">
<style>
  body{margin:0;background:$bg;color:$fg;font:16px/1.5 -apple-system,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif}
  .box{max-width:878px;margin:24px auto;border:1px solid $border;border-radius:6px;padding:24px;box-sizing:border-box}
  .head{font-size:12px;margin:-8px 0 16px;opacity:.7}
  p{margin:0 0 16px} img{max-width:100%;box-sizing:content-box} a{color:$link}
</style>
<div class="box"><div class="head">ZIPender / README.md</div>
$body
</div>
EOF
  echo "file://$PWD/.preview/$theme.html"
done
[[ ${1:-} == --open ]] && xdg-open ".preview/dark.html" >/dev/null 2>&1 &
exit 0
