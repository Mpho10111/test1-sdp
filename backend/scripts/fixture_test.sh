#!/usr/bin/env bash
# Builds a synthetic git fixture with known history and inspects the exact
# byte format of `git diff-tree --stdin -z --numstat` plus mailmap behavior.
set -e

BASE="$(cd "$(dirname "$0")/../.." && pwd)/data/tmp"
FIX="$BASE/fixture"
rm -rf "$BASE" && mkdir -p "$FIX"
cd "$FIX"

export GIT_AUTHOR_DATE="2024-01-01T10:00:00"
export GIT_COMMITTER_DATE="2024-01-01T10:00:00"

git init -q -b main
git config user.name "Alice"
git config user.email "alice@example.com"

echo "line1" > f1.txt
echo "other" > f2.txt
git add .
git commit -qm "c1 root commit"

git mv f1.txt f1r.txt
echo "line2" >> f1r.txt
git commit -qm "c2 rename + edit"

printf '\x00\x01\x02\x03' > bin.dat
git add bin.dat
git commit -qm "c3 add binary"

git rm -q f2.txt
git commit -qm "c4 delete f2"

git commit -q --allow-empty -m "c5 empty commit"

echo x > f3.txt
git add f3.txt
GIT_AUTHOR_NAME="Alice B" GIT_AUTHOR_EMAIL="aliceb@example.com" git commit -qm "c6 second identity"

printf 'Alice <alice@example.com> <aliceb@example.com>\n' > .mailmap
git add .mailmap
git commit -qm "c7 add mailmap"

echo "=== history ==="
git log --oneline --reverse

HASHES="$BASE/hashes.txt"
git rev-list --no-merges --reverse HEAD > "$HASHES"

echo "=== TEST A: diff-tree --stdin -z format (xxd, first 200 lines) ==="
git diff-tree -r --root --numstat -z -M50% --stdin < "$HASHES" | xxd | head -200

echo "=== TEST B: mailmap in bare mirror clone ==="
cd "$BASE"
git clone -q --mirror "$FIX" fixture.git
echo "-- cat-file check .mailmap at HEAD:"
git -C fixture.git cat-file -e "HEAD:.mailmap" && echo "mailmap EXISTS at HEAD:.mailmap"
echo "-- log without mailmap config (%an|%ae|%aN|%aE):"
git -C fixture.git log -1 --format="%an|%ae|%aN|%aE" HEAD
echo "-- log with -c mailmap.blob=HEAD:.mailmap:"
git -C fixture.git -c mailmap.blob=HEAD:.mailmap log -1 --format="%an|%ae|%aN|%aE" HEAD
echo "-- full log line format with NUL (xxd):"
git -C fixture.git -c mailmap.blob=HEAD:.mailmap log --no-merges --reverse --format="%H%x00%ct%x00%P%x00%an%x00%ae%x00%aN%x00%aE" HEAD | head -2 | xxd | head -20
