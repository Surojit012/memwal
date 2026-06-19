#!/bin/sh
set -eu

rm -rf public
mkdir -p public/static public/svg

cp playground/static/index.html public/index.html
cp playground/static/playground.html public/playground.html
cp -R playground/static/css public/static/css
cp -R playground/static/js public/static/js
cp -R playground/svg/* public/svg/
