// Installed by Jest before any application import. No native network permitted.
const attempts = []
function deny(name) {
    return function () {
        attempts.push(name)
        throw new Error('Native network forbidden: ' + name)
    }
}
const net = require('node:net')
net.Socket.prototype.connect = deny('net.Socket.connect:TCP-or-Unix')
net.connect = deny('net.connect')
net.createConnection = deny('net.createConnection')
const tls = require('node:tls')
tls.connect = deny('tls.connect')
for (const moduleName of ['node:http', 'node:https']) {
    const module = require(moduleName)
    module.request = deny(moduleName + '.request')
    module.get = deny(moduleName + '.get')
}
for (const moduleName of ['node:dns', 'node:dns/promises']) {
    const module = require(moduleName)
    for (const key of Object.keys(module)) {
        if (/^(lookup|resolve|reverse)/.test(key) && typeof module[key] === 'function') {
            module[key] = deny(moduleName + '.' + key)
        }
    }
}
require('node:dgram').createSocket = deny('dgram.createSocket')
global.fetch = deny('fetch')
global.__nativeNetworkAttempts = attempts
// Prevent the REAL DockerApi module-level version probe from touching Docker.
jest.mock('dockerode', () => jest.fn(() => ({
    version: jest.fn().mockResolvedValue({ ApiVersion: '1.55' }),
})))
