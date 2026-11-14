// Lint rules for the extension JavaScript. The Shell and gi imports cannot be
// resolved outside the shell, so only syntax-level checks run here.

export default [
    {
        files: ['**/*.js'],
        languageOptions: {
            ecmaVersion: 2023,
            sourceType: 'module',
        },
        rules: {
            'no-unused-vars': ['error', {argsIgnorePattern: '^_'}],
            'no-dupe-keys': 'error',
            'no-unreachable': 'error',
            'no-constant-condition': 'error',
            'no-self-assign': 'error',
            'no-useless-catch': 'error',
            'no-useless-escape': 'error',
            'no-fallthrough': 'error',
            'no-shadow': 'error',
            'eqeqeq': ['error', 'always', {null: 'ignore'}],
        },
    },
];
