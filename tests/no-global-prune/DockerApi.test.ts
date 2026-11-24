// Keep direct/default Jest invocation safe as well as the scoped config.
import './network-deny.cjs'
import Docker = require('dockerode')
import DockerApi from '../../src/docker/DockerApi'

// Real, versioned class and methods; only the Docker transport is synthetic.
const constructor = Docker as unknown as jest.Mock
const nativeAttempts = () =>
    (global as unknown as { __nativeNetworkAttempts: string[] })
        .__nativeNetworkAttempts

function fixture() {
    const original = {
        Version: { Index: '7' },
        Spec: {
            Name: 'synthetic-service',
            Labels: { retained: 'yes' },
            TaskTemplate: {
                ContainerSpec: { Image: 'old:1', Labels: { retained: 'yes' } },
            },
            Mode: { Replicated: { Replicas: 1 } },
        },
    }
    const result = { syntheticResult: true }
    const service = {
        inspect: jest.fn().mockResolvedValue(original),
        update: jest.fn().mockResolvedValue(result),
    }
    const container = {
        stop: jest.fn().mockResolvedValue(undefined),
        wait: jest.fn().mockResolvedValue(undefined),
        remove: jest.fn().mockResolvedValue(undefined),
    }
    const disconnectResult = { syntheticDisconnect: true }
    const network = { disconnect: jest.fn().mockResolvedValue(disconnectResult) }
    const transport = {
        getService: jest.fn(() => service),
        getContainer: jest.fn((_name: string) => container),
        getNetwork: jest.fn(() => network),
        pruneContainers: jest.fn().mockResolvedValue({}),
    }
    constructor.mockImplementationOnce(() => transport)
    const api = new DockerApi({ socketPath: '/synthetic-never-opened.sock' })
    const hook = jest.fn(async (_app, spec) => {
        spec.Labels.hooked = 'yes'
        return spec
    })
    const update = () =>
        api.updateService(
            'synthetic-service', 'new:2', undefined, undefined,
            [{ key: 'SYNTHETIC', value: 'yes' }], undefined, undefined,
            2, undefined, undefined, undefined, undefined, 'stopFirst',
            { Labels: { overridden: 'yes' } }, hook
        )
    return { api, transport, service, container, network, result,
        disconnectResult, hook, original, update }
}

beforeEach(() => jest.useFakeTimers())
afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
    expect(nativeAttempts()).toEqual([])
})

test('module-level Docker version probe was mocked before real module import', async () => {
    await Promise.resolve()
    expect(constructor.mock.calls[1][0].version).toBe('v1.44')
    expect(constructor.mock.results[1].value.version).toHaveBeenCalledTimes(1)
    expect(DockerApi.get()).toBeInstanceOf(DockerApi)
})

test('actual successful update never prunes after five seconds', async () => {
    const f = fixture()
    expect(await f.update()).toBe(f.result)
    expect(f.service.update).toHaveBeenCalledTimes(1)
    expect(f.transport.pruneContainers).not.toHaveBeenCalled()
    await jest.advanceTimersByTimeAsync(4999)
    expect(f.transport.pruneContainers).not.toHaveBeenCalled()
    await jest.advanceTimersByTimeAsync(1)
    expect(f.transport.pruneContainers).toHaveBeenCalledTimes(0)
    await jest.advanceTimersByTimeAsync(60000)
    expect(f.transport.pruneContainers).toHaveBeenCalledTimes(0)
})

test('update retains Spec/auth/override/hook/managed-label/result behavior', async () => {
    const f = fixture()
    expect(await f.update()).toBe(f.result)
    const spec = f.service.update.mock.calls[0][0]
    expect(spec).toMatchObject({
        Name: 'synthetic-service', version: 7,
        Labels: { retained: 'yes', overridden: 'yes', hooked: 'yes', 'com.caprover.managed': 'true' },
        authconfig: { username: '', password: '', serveraddress: 'docker.io/v1' },
        TaskTemplate: { ContainerSpec: { Image: 'new:2', Env: ['SYNTHETIC=yes'], Labels: { retained: 'yes' } } },
        Mode: { Replicated: { Replicas: 2 } }, UpdateConfig: { Order: 'stop-first' },
    })
    expect(spec.TaskTemplate.ContainerSpec.Labels.randomLabelForceUpdate).toMatch(/^[0-9a-f-]{36}$/)
    expect(f.hook).toHaveBeenCalledWith(undefined, spec)
    expect(f.original.Spec.TaskTemplate.ContainerSpec.Image).toBe('old:1')
})

test.each(['inspect', 'update', 'hook'])('update propagates %s failure unchanged without prune', async (stage) => {
    const f = fixture()
    const error = new Error('synthetic failure')
    if (stage === 'hook') f.hook.mockRejectedValueOnce(error)
    else f.service[stage as 'inspect' | 'update'].mockRejectedValueOnce(error)
    await expect(f.update()).rejects.toBe(error)
    await jest.advanceTimersByTimeAsync(6000)
    expect(f.transport.pruneContainers).not.toHaveBeenCalled()
    if (stage !== 'update') expect(f.service.update).not.toHaveBeenCalled()
})

test('actual targeted cleanup never invokes global prune', async () => {
    const f = fixture()
    expect(await f.api.ensureContainerStoppedAndRemoved('old-build', 'build-net')).toBe(f.disconnectResult)
    expect(f.container.stop).toHaveBeenCalledWith({ t: 2 })
    expect(f.container.wait).toHaveBeenCalledTimes(1)
    expect(f.container.remove).toHaveBeenCalledWith({ force: true })
    expect(f.network.disconnect).toHaveBeenCalledWith({ Force: true, Container: 'old-build' })
    expect(f.transport.getContainer.mock.calls.every((args) => args[0] === 'old-build')).toBe(true)
    expect(f.transport.pruneContainers).toHaveBeenCalledTimes(0)
})

test.each(['stop', 'wait', 'remove', 'disconnect'])('cleanup propagates %s failure unchanged', async (stage) => {
    const f = fixture()
    const error = new Error('synthetic cleanup failure')
    if (stage === 'disconnect') f.network.disconnect.mockRejectedValueOnce(error)
    else f.container[stage as 'stop' | 'wait' | 'remove'].mockRejectedValueOnce(error)
    await expect(f.api.ensureContainerStoppedAndRemoved('old-build', 'build-net')).rejects.toBe(error)
    if (stage !== 'disconnect') expect(f.network.disconnect).not.toHaveBeenCalled()
})

test('cleanup retains already-stopped 304 and missing 404 handling', async () => {
    const f = fixture()
    f.container.stop.mockRejectedValueOnce({ statusCode: 304 })
    expect(await f.api.ensureContainerStoppedAndRemoved('old-build', 'build-net')).toBe(f.disconnectResult)
    f.container.remove.mockRejectedValueOnce({ statusCode: 404 })
    await expect(f.api.ensureContainerStoppedAndRemoved('old-build', 'build-net')).resolves.toBe(false)
})

test('explicit prune helper remains available', async () => {
    const f = fixture()
    await f.api.pruneContainers()
    expect(f.transport.pruneContainers).toHaveBeenCalledTimes(1)
})
