afterAll(async () => {
    // Drain the synthetic module-level probe before Jest tears logging down.
    await Promise.resolve()
    expect(global.__nativeNetworkAttempts).toEqual([])
    console.log('NATIVE_NETWORK_ATTEMPTS=' + global.__nativeNetworkAttempts.length)
})
