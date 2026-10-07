'use client'

import { useEffect, useState } from 'react'

/** Link to a PyPI project that shows its latest version once it loads.
 *  "PyPI" is the fallback when the request fails or the package is unpublished. */
export function PypiVersion({ name }: { name: string }) {
  const [version, setVersion] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`https://pypi.org/pypi/${name}/json`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => setVersion(data?.info?.version ?? null))
      .catch(() => {
        // Network error or bad JSON: keep the PyPI fallback
      })
    return () => controller.abort()
  }, [name])

  return <a href={`https://pypi.org/project/${name}/`}>{version ?? 'PyPI'}</a>
}
