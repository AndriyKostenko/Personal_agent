import { createApp } from 'vue'
import App from './App.vue'

// fonts are bundled (no external requests): wide display, monospace labels, body text
import '@fontsource/michroma'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/700.css'
import '@fontsource/space-grotesk/400.css'
import '@fontsource/space-grotesk/500.css'
import '@fontsource/space-grotesk/700.css'

createApp(App).mount('#app')
