// Requires the official npm pyodide@314.0.6 package; installs nothing itself.
// node scripts/probe_numeric_runtime.mjs --pyodide-module /tmp/probe/node_modules/pyodide/pyodide.mjs --output /tmp/wasm.json
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const argumentsMap = Object.fromEntries(Array.from({length:(process.argv.length-2)/2},(_,i)=>process.argv.slice(2+i*2,4+i*2)));
if (!argumentsMap['--pyodide-module'] || !argumentsMap['--output']) {
  throw new Error('Pass --pyodide-module PATH and --output PATH');
}
const repo = path.resolve(argumentsMap['--repo'] || path.join(path.dirname(fileURLToPath(import.meta.url)), '..'));
const {loadPyodide} = await import(pathToFileURL(path.resolve(argumentsMap['--pyodide-module'])).href);
const pyodide = await loadPyodide();
async function copyDirectory(source, target, accept) {
  pyodide.FS.mkdirTree(target);
  for (const entry of await fs.readdir(source, {withFileTypes:true})) {
    if (entry.name==='__pycache__' || !accept(entry.name,entry.isDirectory())) continue;
    if(entry.isDirectory()) await copyDirectory(path.join(source,entry.name), `${target}/${entry.name}`,accept);
    else pyodide.FS.writeFile(`${target}/${entry.name}`,await fs.readFile(path.join(source,entry.name)));
  }
}
const sourceFiles = (name,directory)=>directory || name.endsWith('.py') || name.endsWith('.json');
await copyDirectory(path.join(repo,'norn'),'/project/norn',sourceFiles);
await copyDirectory(path.join(repo,'the-norn-machine/backend'),'/project/the-norn-machine/backend',sourceFiles);
await copyDirectory(path.join(repo,'tests/fixtures'),'/project/tests/fixtures',sourceFiles);
pyodide.FS.mkdirTree('/project/scripts');
pyodide.FS.writeFile('/project/scripts/probe_numeric_runtime.py',await fs.readFile(path.join(repo,'scripts/probe_numeric_runtime.py')));
await pyodide.runPythonAsync(`import sys
sys.argv=['/project/scripts/probe_numeric_runtime.py','--repo','/project','--output','/numeric-output.json']
exec(compile(open(sys.argv[0]).read(),sys.argv[0],'exec'),{'__name__':'__main__','__file__':sys.argv[0]})`);
await fs.writeFile(path.resolve(argumentsMap['--output']),pyodide.FS.readFile('/numeric-output.json'));
console.log(JSON.stringify({wasm_heap_bytes:pyodide._module.HEAPU8.byteLength}));
