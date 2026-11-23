import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// fonts are bundled (no external requests): wide display, monospace labels, body text
import '@fontsource/michroma'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/700.css'
import '@fontsource/space-grotesk/400.css'
import '@fontsource/space-grotesk/500.css'
import '@fontsource/space-grotesk/700.css'

import App from './App'
import './styles/app.css'

const root = document.getElementById('app')
if (!root) throw new Error('#app is missing in index.html')
// createRoot replaces the static first-paint content of #app (index.html)
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
