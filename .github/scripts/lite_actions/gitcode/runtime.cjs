'use strict';
// A JavaScript Action receives the artifact service runtime context. An ordinary
// workflow shell step must not scrape or manufacture that bearer token.
const {spawnSync} = require('node:child_process');
const path = require('node:path');
function run(env = process.env, spawn = spawnSync) {
  const phase = env['INPUT_PHASE'];
  if (!['prepare', 'wait'].includes(phase)) throw new Error('invalid phase');
  const child = {...env,
    LITE_ACTIONS_NODE: process.execPath,
    LITE_ACTIONS_RELEASE_CONFIG: env['INPUT_RELEASE-CONFIG'] || '',
    LITE_ACTIONS_GITCODE_REQUEST: env['INPUT_GITCODE-REQUEST'] || '',
    LITE_ACTIONS_GITCODE_ATTESTATION: env['INPUT_GITCODE-ATTESTATION'] || '',
    LITE_ACTIONS_SOURCE_PROVIDER: 'gitcode',
    LITE_ACTIONS_SDK_ROOT: env['INPUT_SDK-ROOT'] || '',
    LITE_ACTIONS_SDK_SHA: env['INPUT_SDK-SHA'] || '',
    LITE_ACTIONS_GITHUB_TOKEN: env['INPUT_GITHUB-TOKEN'] || '',
    LITE_ACTIONS_GITCODE_TOKEN: env['INPUT_GITCODE-TOKEN'] || '',
    LITE_ACTIONS_CHECKS_KEY: phase === 'prepare' ? (env['INPUT_CHECKS-KEY'] || '') : '',
  };
  delete child['INPUT_CHECKS-KEY'];
  // The checked out control tree is pinned by the workflow and verified again
  // against the reviewed policy by the Python runtime.
  const result = spawn('python3', ['-I', '-B', path.join(__dirname, 'runtime.py'), phase], {
    env: child, stdio: 'inherit', shell: false,
  });
  if (result.error || !Number.isInteger(result.status)) return 1;
  return result.status;
}
module.exports = {run};
if (require.main === module) {
  try { process.exitCode = run(); }
  catch { process.stderr.write('GitCode Hosted Action failed closed\n'); process.exitCode = 1; }
}
