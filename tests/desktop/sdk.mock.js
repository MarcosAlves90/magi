// Minimal host-provided SDK hook contract for a rendered, isolated Magi test.
// Test data and requests come only from tests' in-memory ctx.rest fixture.
import { useCallback, useEffect, useRef, useState } from 'react'

export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar'

export function useQuery({ queryKey, queryFn, enabled = true }) {
  const [state, setState] = useState({ data: undefined, isLoading: enabled, isFetching: enabled, isError: false })
  const latestFn = useRef(queryFn)
  latestFn.current = queryFn
  const generation = useRef(0)
  const refetch = useCallback(async () => {
    const stamp = ++generation.current
    setState(previous => ({ ...previous, isLoading: previous.data === undefined, isFetching: true, isError: false }))
    try {
      const data = await latestFn.current()
      if (stamp === generation.current) setState({ data, isLoading: false, isFetching: false, isError: false })
      return { data }
    } catch (error) {
      if (stamp === generation.current) setState(previous => ({ ...previous, isLoading: false, isFetching: false, isError: true }))
      return { error }
    }
  }, [])
  const key = JSON.stringify(queryKey)
  useEffect(() => {
    generation.current += 1
    if (enabled) void refetch()
    return () => { generation.current += 1 }
  }, [key, enabled, refetch])
  return { ...state, refetch }
}
