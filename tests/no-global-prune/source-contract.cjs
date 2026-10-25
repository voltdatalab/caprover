const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const cp = require('node:child_process')
const crypto = require('node:crypto')
const ts = require('typescript')
const base = '5a5c89c67ea819faf2032a77649eaf84c106f7da'
const file = 'src/docker/DockerApi.ts'
const original = cp.execFileSync('git', ['show', `${base}:${file}`], { encoding: 'utf8' })
const current = fs.readFileSync(file, 'utf8')
const removed = [
    '            .then(function () {\n                return self.pruneContainers()\n            })\n',
    '            .then(function (serviceData) {\n                // give some time such that the new container is updated.\n                // also we don\'t want to fail the update just because prune failed.\n                setTimeout(function () {\n                    self.pruneContainers()\n                }, 5000)\n\n                return serviceData\n            })\n',
]
let expected = original
for (const block of removed) {
    assert.equal(expected.split(block).length, 2)
    expected = expected.replace(block, '')
}
assert.equal(current, expected, 'Only the two automatic prune continuations may change')
const inventory = []
function inspect(name, source, phase) {
    const ast = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true)
    function visit(node) {
        if ((ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) &&
            /prune|ensureContainerStoppedAndRemoved|buildImageFromDockerFile|updateService/.test(node.getText(ast))) {
            const member = ts.isPropertyAccessExpression(node) ? node.name.text : node.argumentExpression.getText(ast)
            if (/^(prune\w*|ensureContainerStoppedAndRemoved|buildImageFromDockerFile|updateService)$/.test(member)) {
                let owner = node.parent
                while (owner && !ts.isMethodDeclaration(owner)) owner = owner.parent
                inventory.push({ phase, file: name, member, owner: owner?.name.getText(ast) || '<module>',
                    expression: node.getText(ast), line: ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1 })
            }
        }
        ts.forEachChild(node, visit)
    }
    visit(ast)
}
function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const name = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(name)
        else if (name.endsWith('.ts')) inspect(name, fs.readFileSync(name, 'utf8'), 'candidate')
    }
}
walk('src')
inspect(file, original, 'base')
const remaining = inventory.filter(x => x.phase === 'candidate' && /^prune/.test(x.member))
assert.deepEqual(remaining.map(x => [x.file, x.member, x.owner]), [[file, 'pruneContainers', 'pruneContainers']])
assert.equal(inventory.filter(x => x.phase === 'base' && x.member === 'pruneContainers').length, 3)
const changedSource = cp.execFileSync('git', ['diff', base, '--name-only', '--', 'src'], { encoding: 'utf8' }).trim().split('\n')
assert.deepEqual(changedSource, [file])
const sha256 = text => crypto.createHash('sha256').update(text).digest('hex')
console.log(JSON.stringify({ base, source: file, originalSha256: sha256(original), candidateSha256: sha256(current),
    automaticPruneReferences: 0, explicitHelperTransportReferences: 1, inventory,
    buildCleanup: 'ImageMaker.buildImageFromDockerFile delegates to dockerode.buildImage with t/buildargs/version and optional registryconfig; no explicit global prune or rm option. createContainer AutoRemove unchanged. ensureContainerStoppedAndRemoved callers are CaptainManager.updateNetDataInfo/updateGoAccessInfo (including startup), not image builds.',
    remainingScheduledCleanup: 'DiskCleanupManager.resetScheduledTasks may schedule onCleanupCalled -> getUnusedImages -> deleteImages by configured cron; DockerApi.deleteImages removes individual image IDs. This host-wide image-selection behavior/configuration is unchanged, not certified disabled and is not container prune. No scheduleDiskCleanup identifier exists in this exact source.' }, null, 2))
