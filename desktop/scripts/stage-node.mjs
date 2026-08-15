import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { cp, mkdir, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifest = JSON.parse(
  await readFile(path.join(desktopRoot, 'resources/runtime-manifest.json'), 'utf8'),
);
const version = manifest.runtimes.node.version;
const target = process.argv[2] ?? `${process.platform}-${process.arch}`;

const archiveByTarget = {
  'darwin-arm64': `node-v${version}-darwin-arm64.tar.gz`,
  'darwin-x64': `node-v${version}-darwin-x64.tar.gz`,
  'win32-x64': `node-v${version}-win-x64.zip`,
};

const archiveName = archiveByTarget[target];
if (!archiveName) {
  throw new Error(`Target sem distribuicao Node configurada: ${target}`);
}

const cacheDirectory = path.join(desktopRoot, '.runtime-cache', 'node', version);
const archivePath = path.join(cacheDirectory, archiveName);
const destination = path.join(desktopRoot, 'resources', 'runtimes', target, 'node');
const baseUrl = `https://nodejs.org/dist/v${version}`;

await mkdir(cacheDirectory, { recursive: true });

async function download(url, output) {
  const response = await fetch(url);
  if (!response.ok || !response.body) {
    throw new Error(`Download falhou (${response.status}): ${url}`);
  }
  await pipeline(Readable.fromWeb(response.body), createWriteStream(output));
}

async function sha256(filePath) {
  const hash = createHash('sha256');
  await pipeline(createReadStream(filePath), hash);
  return hash.digest('hex');
}

const sumsResponse = await fetch(`${baseUrl}/SHASUMS256.txt`);
if (!sumsResponse.ok) throw new Error('Nao foi possivel obter SHASUMS256.txt do Node.');
const sums = await sumsResponse.text();
const expectedHash = sums
  .split(/\r?\n/)
  .find((line) => line.endsWith(`  ${archiveName}`))
  ?.split(/\s+/, 1)[0];
if (!expectedHash) throw new Error(`Checksum oficial ausente para ${archiveName}.`);

let currentHash = null;
try {
  currentHash = await sha256(archivePath);
} catch {
  // Cache miss: download below.
}

if (currentHash !== expectedHash) {
  await rm(archivePath, { force: true });
  console.log(`Baixando Node ${version} para ${target}...`);
  await download(`${baseUrl}/${archiveName}`, archivePath);
  currentHash = await sha256(archivePath);
}

if (currentHash !== expectedHash) {
  await rm(archivePath, { force: true });
  throw new Error(`Checksum invalido para ${archiveName}.`);
}

const temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'edvid-node-'));
try {
  const extract = spawnSync('tar', ['-xf', archivePath, '-C', temporaryDirectory], {
    stdio: 'inherit',
  });
  if (extract.status !== 0) throw new Error(`Falha ao extrair ${archiveName}.`);

  const extractedRoot = path.join(
    temporaryDirectory,
    archiveName.replace(/\.tar\.gz$|\.zip$/u, ''),
  );
  await rm(destination, { recursive: true, force: true });
  await mkdir(path.dirname(destination), { recursive: true });
  await cp(extractedRoot, destination, { recursive: true, verbatimSymlinks: true });
  console.log(`Node ${version} preparado em resources/runtimes/${target}/node`);
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}
