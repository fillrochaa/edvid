import type { ForgeConfig } from '@electron-forge/shared-types';
import { MakerDMG } from '@electron-forge/maker-dmg';
import { MakerSquirrel } from '@electron-forge/maker-squirrel';
import { MakerZIP } from '@electron-forge/maker-zip';
import { FusesPlugin } from '@electron-forge/plugin-fuses';
import { VitePlugin } from '@electron-forge/plugin-vite';
import { FuseV1Options, FuseVersion } from '@electron/fuses';
import path from 'node:path';

const macSigningIdentity = process.env.EDVID_MAC_SIGN_IDENTITY?.trim();

const config: ForgeConfig = {
  packagerConfig: {
    appBundleId: 'com.creatorfactory.edvid',
    appCategoryType: 'public.app-category.video',
    asar: true,
    extraResource: ['resources/runtime-manifest.json', 'resources/runtimes'],
    osxSign:
      process.platform === 'darwin'
        ? {
            identity: macSigningIdentity || '-',
            identityValidation: Boolean(macSigningIdentity),
            optionsForFile: macSigningIdentity
              ? undefined
              : () => ({ entitlements: path.resolve('entitlements.mac.dev.plist') }),
          }
        : undefined,
  },
  rebuildConfig: {},
  makers: [
    new MakerSquirrel({ name: 'edvid' }),
    new MakerDMG({}, ['darwin']),
    new MakerZIP({}, ['darwin']),
  ],
  plugins: [
    new VitePlugin({
      build: [
        {
          entry: 'src/main.ts',
          config: 'vite.main.config.ts',
          target: 'main',
        },
        {
          entry: 'src/preload.ts',
          config: 'vite.preload.config.ts',
          target: 'preload',
        },
      ],
      renderer: [
        {
          name: 'main_window',
          config: 'vite.renderer.config.ts',
        },
      ],
    }),
    new FusesPlugin({
      version: FuseVersion.V1,
      [FuseV1Options.RunAsNode]: false,
      [FuseV1Options.EnableCookieEncryption]: true,
      [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
      [FuseV1Options.EnableNodeCliInspectArguments]: false,
      [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
      [FuseV1Options.OnlyLoadAppFromAsar]: true,
    }),
  ],
};

export default config;
