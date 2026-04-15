#!/usr/bin/env node

const { execSync, spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const https = require('https');

const VERSION = '3.10.0';
const BINARY_NAME = 'heliosdb-nano';
const GITHUB_REPO = 'Dimensigon/HDB-HeliosDB-Nano';
const INSTALL_DIR = path.join(os.homedir(), '.heliosdb', 'bin');

// Platform detection
function getPlatformTarget() {
  const platform = process.platform;
  const arch = process.arch;
  if (platform === 'darwin' && arch === 'arm64') return 'aarch64-apple-darwin';
  if (platform === 'darwin' && arch === 'x64') return 'x86_64-apple-darwin';
  if (platform === 'linux' && arch === 'x64') return 'x86_64-unknown-linux-gnu';
  if (platform === 'linux' && arch === 'arm64') return 'aarch64-unknown-linux-gnu';
  console.error(`Unsupported platform: ${platform}-${arch}`);
  process.exit(1);
}

function getBinaryPath() {
  const ext = process.platform === 'win32' ? '.exe' : '';
  return path.join(INSTALL_DIR, `${BINARY_NAME}${ext}`);
}

function isBinaryInstalled() {
  return fs.existsSync(getBinaryPath());
}

async function downloadBinary() {
  const target = getPlatformTarget();
  const url = `https://github.com/${GITHUB_REPO}/releases/latest/download/${BINARY_NAME}-${target}.tar.gz`;

  console.log(`Downloading HeliosDB Nano for ${target}...`);
  console.log(`From: ${url}`);

  fs.mkdirSync(INSTALL_DIR, { recursive: true });

  try {
    execSync(`curl -fsSL "${url}" | tar xz -C "${INSTALL_DIR}"`, { stdio: 'inherit' });
    fs.chmodSync(getBinaryPath(), 0o755);
    console.log(`Installed to ${getBinaryPath()}`);
  } catch (e) {
    console.error('Download failed. You can install manually:');
    console.error(`  cargo install heliosdb-nano`);
    console.error(`  # or download from https://github.com/${GITHUB_REPO}/releases`);
    process.exit(1);
  }
}

function ensureBinary() {
  if (!isBinaryInstalled()) {
    console.log('HeliosDB Nano binary not found. Installing...');
    downloadBinary();
  }
}

// Commands
const commands = {
  init() {
    console.log('Initializing HeliosDB project...');
    fs.mkdirSync('migrations', { recursive: true });
    if (!fs.existsSync('heliosdb.config.json')) {
      fs.writeFileSync('heliosdb.config.json', JSON.stringify({
        port: 5432,
        http_port: 8080,
        mysql_port: 3306,
        data_dir: './heliosdb-data',
        auth: { jwt_secret: require('crypto').randomBytes(32).toString('hex') }
      }, null, 2));
    }
    if (!fs.existsSync('.gitignore')) {
      fs.writeFileSync('.gitignore', 'heliosdb-data/\n.env\n');
    } else {
      const content = fs.readFileSync('.gitignore', 'utf8');
      if (!content.includes('heliosdb-data')) {
        fs.appendFileSync('.gitignore', '\nheliosdb-data/\n');
      }
    }
    console.log('Created:');
    console.log('  heliosdb.config.json');
    console.log('  migrations/');
    console.log('\nRun: npx heliosdb start');
  },

  start() {
    ensureBinary();
    const config = fs.existsSync('heliosdb.config.json')
      ? JSON.parse(fs.readFileSync('heliosdb.config.json', 'utf8'))
      : {};

    const args = [
      'start',
      '--data-dir', config.data_dir || './heliosdb-data',
      '--port', String(config.port || 5432),
      '--listen', '127.0.0.1',
      '--mysql',
      '--mysql-listen', `127.0.0.1:${config.mysql_port || 3306}`,
    ];

    console.log(`Starting HeliosDB Nano v${VERSION}...`);
    const child = spawn(getBinaryPath(), args, { stdio: 'inherit' });
    child.on('exit', (code) => process.exit(code || 0));
  },

  studio() {
    const port = 8080;
    console.log(`Opening HeliosDB Studio at http://localhost:${port}/docs`);
    const opener = process.platform === 'darwin' ? 'open' : process.platform === 'win32' ? 'start' : 'xdg-open';
    try { execSync(`${opener} http://localhost:${port}/docs`); } catch {}
  },

  version() {
    console.log(`heliosdb CLI v${VERSION}`);
    if (isBinaryInstalled()) {
      try {
        execSync(`"${getBinaryPath()}" --version`, { stdio: 'inherit' });
      } catch {}
    }
  },

  help() {
    console.log(`
HeliosDB CLI v${VERSION}

Usage: npx heliosdb <command>

Commands:
  init      Scaffold a new HeliosDB project
  start     Start the local database server
  studio    Open the API explorer (Swagger UI)
  version   Show version info
  help      Show this help

Getting started:
  npx heliosdb init
  npx heliosdb start

Docs: https://github.com/${GITHUB_REPO}
`);
  }
};

// Parse command
const cmd = process.argv[2] || 'help';
if (commands[cmd]) {
  commands[cmd]();
} else {
  console.error(`Unknown command: ${cmd}`);
  commands.help();
  process.exit(1);
}
