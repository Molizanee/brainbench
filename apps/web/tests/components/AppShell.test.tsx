import { expect, test } from 'bun:test'
import { screen } from '@testing-library/react'
import { routes } from '../../src/routes'
import { renderRoutes } from '../harness'

test('shell renders navigation from the route table and the section page', async () => {
  renderRoutes(routes, '/')
  expect(await screen.findByRole('heading', { name: 'Providers' })).toBeDefined()
  const nav = screen.getByRole('navigation', { name: 'Main navigation' })
  expect(nav.textContent).toContain('Providers')
})
