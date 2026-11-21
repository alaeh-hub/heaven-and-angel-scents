import { lazy, Suspense, useEffect } from 'react';
import { Route, Routes, useLocation } from 'react-router';
import { AnimatePresence, motion } from 'motion/react';
import BackToTop from './components/BackToTop.jsx';
import ScrollProgress from './components/ScrollProgress.jsx';
import { InquiryProvider } from './components/InquiryProvider.jsx';
import { ToastProvider } from './components/Toasts.jsx';
import { useGlowPointer } from './hooks/useGlowPointer.js';
import { useScrollReveal } from './hooks/useScrollReveal.js';
import { EASE_OUT } from './motion.js';
import PackagesPage from './pages/PackagesPage.jsx';

// Landing ships in the main bundle; other pages load in the background when idle.
const loadDialog = () => import('./pages/DetailDialog.jsx');
const loadPackagePage = () => import('./pages/PackageDetailPage.jsx');
const loadProducts = () => import('./pages/ProductsPage.jsx');
const DetailDialog = lazy(loadDialog);
const PackageDetailPage = lazy(loadPackagePage);
const ProductsPage = lazy(loadProducts);

function usePrefetchPages() {
  useEffect(() => {
    const run = () => { loadDialog(); loadPackagePage(); loadProducts(); };
    if ('requestIdleCallback' in window) {
      const id = window.requestIdleCallback(run, { timeout: 4000 });
      return () => window.cancelIdleCallback(id);
    }
    const id = setTimeout(run, 2500);
    return () => clearTimeout(id);
  }, []);
}

const LIST = '/partner-portal/:slug/packages';
const DETAIL = '/partner-portal/:slug/packages/:packageId';
const PRODUCTS = '/partner-portal/:slug/products';

/**
 * Routes. A package opened from the list renders in a dialog over it
 * (`state.background`); a shared link or new tab gets the standalone page.
 * Page changes cross-fade with opacity only (a transform would re-anchor
 * the fixed backdrops), keyed by pathname.
 */
export default function App() {
  const location = useLocation();
  const background = location.state?.background;
  const pageLocation = background || location;
  useGlowPointer();
  useScrollReveal();
  usePrefetchPages();

  return (
    <ToastProvider>
      <InquiryProvider>
        <AnimatePresence mode="wait" initial={false} onExitComplete={() => window.scrollTo(0, 0)}>
          <motion.div
            key={pageLocation.pathname}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1, transition: { duration: 0.35, ease: EASE_OUT } }}
            exit={{ opacity: 0, transition: { duration: 0.2, ease: 'easeIn' } }}
          >
            <Suspense fallback={null}>
            <Routes location={pageLocation}>
              <Route path={LIST} element={<PackagesPage />} />
              <Route path={DETAIL} element={<PackageDetailPage />} />
              <Route path={PRODUCTS} element={<ProductsPage />} />
              <Route path="*" element={<p style={{ padding: 40 }}>That page doesn&apos;t exist.</p>} />
            </Routes>
            </Suspense>
          </motion.div>
        </AnimatePresence>
        <ScrollProgress />
        <BackToTop />
        {background && (
          <Suspense fallback={null}>
            <Routes>
              <Route path={DETAIL} element={<DetailDialog />} />
            </Routes>
          </Suspense>
        )}
      </InquiryProvider>
    </ToastProvider>
  );
}
