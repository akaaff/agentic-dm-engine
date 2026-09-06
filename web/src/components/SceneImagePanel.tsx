import { useState } from 'react'

const MEDIA_BASE_URL = 'http://localhost:8000'

// Split out so `key={url}` (below) remounts a fresh instance per image URL -
// `loaded` then naturally starts false for each new image without an effect
// resetting it, avoiding the "setState in effect" anti-pattern entirely.
function SceneImage({ url }: { url: string }) {
  const [loaded, setLoaded] = useState(false)
  return (
    <>
      {!loaded && <p className="scene-image-loading">Rendering the scene...</p>}
      <img
        src={`${MEDIA_BASE_URL}${url}`}
        alt="Current scene"
        className={loaded ? 'scene-image visible' : 'scene-image'}
        onLoad={() => setLoaded(true)}
      />
    </>
  )
}

export default function SceneImagePanel({ url }: { url: string | null }) {
  if (!url) {
    return (
      <div className="scene-image-panel empty">
        <p>The scene will be illustrated as the encounter unfolds.</p>
      </div>
    )
  }

  return (
    <div className="scene-image-panel">
      <SceneImage key={url} url={url} />
    </div>
  )
}
