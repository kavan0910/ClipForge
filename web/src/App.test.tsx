import { render, screen } from '@testing-library/react'
import App from './App'

test('states the privacy promise', () => {
  render(<App />)
  expect(screen.getByText(/stays on this computer/i)).toBeInTheDocument()
})
