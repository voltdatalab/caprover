import './network-deny.cjs'
import Docker = require('dockerode')
import { PassThrough } from 'stream'
import DockerApi from '../../src/docker/DockerApi'
import BuildLog from '../../src/user/BuildLog'

// Proof coverage of the existing build flow, not a behavioral RED claim.
test('real buildImageFromDockerFile preserves targeted build/tag flow without prune', async () => {
    jest.useFakeTimers()
    try {
        const stream = new PassThrough()
        const result = { syntheticTag: true }
        const tag = jest.fn().mockResolvedValue(result)
        const transport = {
            buildImage: jest.fn().mockResolvedValue(stream),
            getImage: jest.fn(() => ({ tag })),
            pruneContainers: jest.fn().mockResolvedValue({}),
        }
        ;(Docker as unknown as jest.Mock).mockImplementationOnce(() => transport)
        const api = new DockerApi({ socketPath: '/synthetic-never-opened.sock' })
        const log = jest.fn()
        const promise = api.buildImageFromDockerFile(
            'synthetic-build', 2, '/synthetic-never-read.tar',
            { log } as unknown as BuildLog,
            [{ key: 'SYNTHETIC', value: 'yes' }], {}
        )
        await jest.advanceTimersByTimeAsync(0)
        stream.end('{"stream":"synthetic complete"}\n')
        expect(await promise).toBe(result)
        expect(transport.buildImage).toHaveBeenCalledWith('/synthetic-never-read.tar', {
            t: 'synthetic-build', buildargs: { SYNTHETIC: 'yes' }, version: '2',
        })
        expect(tag).toHaveBeenCalledWith({ repo: 'synthetic-build', tag: '2' })
        await jest.advanceTimersByTimeAsync(60000)
        expect(transport.pruneContainers).not.toHaveBeenCalled()
    } finally {
        jest.clearAllTimers()
        jest.useRealTimers()
    }
})

test('real build preserves Docker transport failure identity', async () => {
    const error = new Error('synthetic build failure')
    const transport = { buildImage: jest.fn().mockRejectedValue(error), pruneContainers: jest.fn() }
    ;(Docker as unknown as jest.Mock).mockImplementationOnce(() => transport)
    const api = new DockerApi({ socketPath: '/synthetic-never-opened.sock' })
    await expect(api.buildImageFromDockerFile('synthetic-build', 2, '/synthetic.tar',
        { log: jest.fn() } as unknown as BuildLog, [], {})).rejects.toBe(error)
    expect(transport.pruneContainers).not.toHaveBeenCalled()
})
