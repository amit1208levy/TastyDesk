import type { CapacitorConfig } from '@capacitor/cli'

/* The iPhone app: the same pages as the Mac dashboard, with the Mac's Python
   engine inside the app (see ../ios/README.md). It needs no Mac and no server. */
const config: CapacitorConfig = {
  // Must match the bundle identifier registered in App Store Connect.
  // Change it here and in the Xcode project if this one is taken.
  appId: 'com.tastydesk.app',
  appName: 'Tasty Desk',
  webDir: 'dist',
  ios: {
    // The pages handle the safe areas themselves (viewport-fit=cover).
    contentInset: 'never',
    // Nothing to inspect in a build that goes to TestFlight.
    webContentsDebuggingEnabled: false,
  },
}

export default config
