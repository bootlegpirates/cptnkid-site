// Rebuilds data/random.json (the single file the site fetches) from the
// per-tile files in data/random/*.json written by the Sveltia folder collection.
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';

const DIR = 'data/random';
const OUT = 'data/random.json';

const files = existsSync(DIR) ? readdirSync(DIR).filter((f) => f.endsWith('.json')) : [];

const tiles = files.map((file) => ({ file, ...JSON.parse(readFileSync(`${DIR}/${file}`, 'utf8')) }));

tiles.sort((a, b) => {
  const ao = Number.isFinite(a.order) ? a.order : Infinity;
  const bo = Number.isFinite(b.order) ? b.order : Infinity;
  if (ao !== bo) return ao - bo;          // explicit order first
  return a.file.localeCompare(b.file);    // then filename (creation time)
});

const links = tiles
  .map(({ url, kind, thumb }) => {
    const tile = { url: (url || '').trim(), kind: kind === 'image' ? 'image' : 'video' };
    if (thumb) tile.thumb = thumb;
    return tile;
  })
  .filter((t) => t.url);

writeFileSync(OUT, JSON.stringify({ links }, null, 2) + '\n');
console.log(`Wrote ${OUT} with ${links.length} tile(s).`);
