const base = require('../../jest.config.js')
module.exports = {
    ...base,
    collectCoverage: false,
    setupFiles: ['<rootDir>/tests/no-global-prune/network-deny.cjs'],
    setupFilesAfterEnv: ['<rootDir>/tests/no-global-prune/network-assert.cjs'],
    testRegex: undefined,
    testPathIgnorePatterns: ['/node_modules/'],
    testMatch: [
        '<rootDir>/tests/no-global-prune/DockerApi.test.ts',
        '<rootDir>/tests/no-global-prune/BuildCleanup.test.ts',
        ...['DockerBuildOutput', 'dockerutil', 'utilsMerge', 'CircularQueue',
            'ApacheMd5', 'encryptor', 'utilsJsonYaml', 'ServiceNamingMigration']
            .map(name => `<rootDir>/tests/${name}.test.ts`),
    ],
    rootDir: '../..',
}
