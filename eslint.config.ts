import { base, pulumi } from '@branchleft/eslint-config';
import type { Linter } from 'eslint';

// `vendor/` is an untracked checkout of ghost-platform, and the dot-directories
// hold agent worktrees: none of it is this repo's code, as tsconfig.json says.
const ignores: Linter.Config = { ignores: ['node_modules', 'vendor', '.claude', '.worktrees'] };

// `unicorn/name-replacements` is switched off where the names are the stack's
// contract rather than abbreviations to expand: `imageRef`, `secretsEnvTemplate`,
// `imageEnvFile`, `secretsEnvPath` and `imageEnvPath` are exported stack outputs
// and config keys, and the tests address them by those exact names.
const contractNames: Linter.Config = {
  files: ['index.ts', 'config.ts', 'test/**/*.ts'],
  rules: { 'unicorn/name-replacements': 'off' },
};

export default [...base, ...pulumi, contractNames, ignores];
