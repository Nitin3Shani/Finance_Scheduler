import React, { useState, useEffect } from 'react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend 
} from 'recharts';
import { 
  Wallet, Search, Plus, Bell, RefreshCw, Trash2, 
  LogIn, UserPlus, AlertCircle, ShoppingBag, BarChart2, Sun, Moon
} from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

export default function App() {
  const [theme, setTheme] = useState(localStorage.getItem('theme') || 'light');
  const [token, setToken] = useState(localStorage.getItem('token') || null);
  const [authMode, setAuthMode] = useState('login');
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authError, setAuthError] = useState('');

  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [productHistory, setProductHistory] = useState(null);
  const [loading, setLoading] = useState(false);
  const [refreshingId, setRefreshingId] = useState(null);

  const [showAddModal, setShowAddModal] = useState(false);
  const [showAlertModal, setShowAlertModal] = useState(false);
  const [alertTargetPrice, setAlertTargetPrice] = useState('');

  // Live Search & Compare Modal State
  const [showSearchModal, setShowSearchModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState(null);
  const [searchLoading, setSearchLoading] = useState(false);

  const [formData, setFormData] = useState({
    title: '',
    image_url: '',
    amazon_url: '',
    flipkart_url: '',
    initial_amazon_price: '',
    initial_flipkart_price: ''
  });

  const toggleTheme = () => {
    const nextTheme = theme === 'light' ? 'dark' : 'light';
    setTheme(nextTheme);
    localStorage.setItem('theme', nextTheme);
  };

  useEffect(() => {
    if (token) {
      fetchProducts();
    }
  }, [token]);

  const handleAuth = async (e) => {
    e.preventDefault();
    setAuthError('');

    try {
      if (authMode === 'register') {
        const res = await fetch(`${API_BASE}/auth/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: authEmail, password: authPassword })
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Registration failed');
        }

        const bodyFormData = new FormData();
        bodyFormData.append('username', authEmail);
        bodyFormData.append('password', authPassword);

        const loginRes = await fetch(`${API_BASE}/auth/login`, {
          method: 'POST',
          body: bodyFormData
        });

        if (!loginRes.ok) throw new Error('Registered successfully! Please sign in.');
        const data = await loginRes.json();
        setToken(data.access_token);
        localStorage.setItem('token', data.access_token);
      } else {
        const bodyFormData = new FormData();
        bodyFormData.append('username', authEmail);
        bodyFormData.append('password', authPassword);

        const res = await fetch(`${API_BASE}/auth/login`, {
          method: 'POST',
          body: bodyFormData
        });
        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Invalid email or password');
        }
        const data = await res.json();
        setToken(data.access_token);
        localStorage.setItem('token', data.access_token);
      }
    } catch (err) {
      setAuthError(err.message);
    }
  };

  const handleLogout = () => {
    setToken(null);
    localStorage.removeItem('token');
    setProducts([]);
    setSelectedProduct(null);
  };

  const fetchProducts = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/products/`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const rawProducts = await res.json();
        
        // Fetch detailed history for each product to extract recent prices
        const enrichedProducts = await Promise.all(
          rawProducts.map(async (prod) => {
            try {
              const detailRes = await fetch(`${API_BASE}/products/${prod.id}`, {
                headers: { Authorization: `Bearer ${token}` }
              });
              if (detailRes.ok) {
                const detailData = await detailRes.json();
                const chartData = detailData.chart_data || [];
                const latestPoint = chartData.length > 0 ? chartData[chartData.length - 1] : {};
                
                return {
                  ...prod,
                  resolved_amazon_price: latestPoint.amazon_price ?? detailData.latest_amazon_price ?? null,
                  resolved_flipkart_price: latestPoint.flipkart_price ?? detailData.latest_flipkart_price ?? null
                };
              }
            } catch (err) {
              console.error(`Error fetching detail for product ${prod.id}`, err);
            }
            return prod;
          })
        );
        
        setProducts(enrichedProducts);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async (e) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    setSearchLoading(true);
    setSearchResults(null);
    try {
      const res = await fetch(`${API_BASE}/products/search?query=${encodeURIComponent(searchQuery)}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setSearchResults(data);
      } else {
        const err = await res.json();
        const errorMessage = typeof err.detail === 'string' 
          ? err.detail 
          : Array.isArray(err.detail) 
            ? err.detail.map(d => `${d.loc.join('.')}: ${d.msg}`).join('\n')
            : 'Validation error occurred';
        alert(`Search Error (${res.status}):\n${errorMessage}`);
      }
    } catch (err) {
      console.error(err);
      alert('Network error executing search');
    } finally {
      setSearchLoading(false);
    }
  };

  const handleAddProduct = async (e) => {
    e.preventDefault();
    if (!formData.title.trim()) {
      alert("Product Title is required.");
      return;
    }
    if (!formData.initial_amazon_price && !formData.initial_flipkart_price) {
      alert("Initial price for at least one platform is required.");
      return;
    }

    const payload = {
      title: formData.title,
      image_url: formData.image_url || null,
      amazon_url: formData.amazon_url || null,
      flipkart_url: formData.flipkart_url || null,
      amazon_price: formData.initial_amazon_price ? parseFloat(formData.initial_amazon_price) : null,
      flipkart_price: formData.initial_flipkart_price ? parseFloat(formData.initial_flipkart_price) : null,
      initial_amazon_price: formData.initial_amazon_price ? parseFloat(formData.initial_amazon_price) : null,
      initial_flipkart_price: formData.initial_flipkart_price ? parseFloat(formData.initial_flipkart_price) : null
    };

    try {
      const res = await fetch(`${API_BASE}/products/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        fetchProducts();
        setShowAddModal(false);
        resetForm();
      } else {
        const err = await res.json();
        const errorMessage = typeof err.detail === 'string'
          ? err.detail
          : Array.isArray(err.detail)
            ? err.detail.map(d => `${d.loc.join('.')}: ${d.msg}`).join('\n')
            : 'Failed to track product';
        alert(`Error (${res.status}):\n${errorMessage}`);
      }
    } catch (err) {
      alert('Network error adding product');
    }
  };

  const handleRefresh = async (id, e) => {
    e.stopPropagation();
    setRefreshingId(id);
    try {
      await fetch(`${API_BASE}/products/${id}/refresh`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` }
      });
      fetchProducts();
      if (selectedProduct && selectedProduct.id === id) {
        fetchProductHistory(selectedProduct);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setRefreshingId(null);
    }
  };

  const handleDelete = async (id, e) => {
    e.stopPropagation();
    if (!confirm('Are you sure you want to delete this product?')) return;
    try {
      const res = await fetch(`${API_BASE}/products/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        setProducts(products.filter((p) => p.id !== id));
        if (selectedProduct?.id === id) setSelectedProduct(null);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const fetchProductHistory = async (product) => {
    setSelectedProduct(product);
    try {
      const res = await fetch(`${API_BASE}/products/${product.id}`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (res.ok) {
        const data = await res.json();
        setProductHistory(data);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleCreateAlert = async (e) => {
    e.preventDefault();
    if (!alertTargetPrice || !selectedProduct) return;
    try {
      const res = await fetch(`${API_BASE}/alerts/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`
        },
        body: JSON.stringify({
          product_id: selectedProduct.id,
          target_price: parseFloat(alertTargetPrice)
        })
      });
      if (res.ok) {
        alert('Alert created successfully!');
        setShowAlertModal(false);
        setAlertTargetPrice('');
      }
    } catch (err) {
      alert('Failed to set price alert');
    }
  };

  const resetForm = () => {
    setFormData({
      title: '',
      image_url: '',
      amazon_url: '',
      flipkart_url: '',
      initial_amazon_price: '',
      initial_flipkart_price: ''
    });
  };

  const isLight = theme === 'light';

  if (!token) {
    return (
      <div className={`min-h-screen flex flex-col justify-center items-center p-4 transition-colors ${
        isLight ? 'bg-slate-100 text-slate-800' : 'bg-slate-950 text-slate-100'
      }`}>
        <div className="absolute top-4 right-4">
          <button
            onClick={toggleTheme}
            className={`p-2 rounded-xl border transition ${
              isLight ? 'bg-white border-slate-300 text-slate-700 hover:bg-slate-50' : 'bg-slate-900 border-slate-800 text-slate-300 hover:bg-slate-800'
            }`}
          >
            {isLight ? <Moon className="w-5 h-5" /> : <Sun className="w-5 h-5" />}
          </button>
        </div>

        <div className={`w-full max-w-md border rounded-2xl p-8 shadow-xl transition-colors ${
          isLight ? 'bg-white border-slate-200 shadow-slate-200/50' : 'bg-slate-900 border-slate-800 shadow-2xl'
        }`}>
          <div className="flex items-center justify-center gap-3 mb-6">
            <div className="p-2.5 bg-indigo-600 rounded-xl text-white shadow-md shadow-indigo-500/20">
              <Wallet className="w-7 h-7" />
            </div>
            <h1 className="text-2xl font-extrabold tracking-tight text-indigo-600">
              Finance Tracker
            </h1>
          </div>

          <div className={`flex p-1 rounded-xl mb-6 border ${
            isLight ? 'bg-slate-100 border-slate-200' : 'bg-slate-950 border-slate-800'
          }`}>
            <button
              onClick={() => setAuthMode('login')}
              className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-all ${
                authMode === 'login' 
                  ? 'bg-indigo-600 text-white shadow-md' 
                  : isLight ? 'text-slate-600 hover:text-slate-900' : 'text-slate-400 hover:text-white'
              }`}
            >
              Sign In
            </button>
            <button
              onClick={() => setAuthMode('register')}
              className={`flex-1 py-2 text-sm font-semibold rounded-lg transition-all ${
                authMode === 'register' 
                  ? 'bg-indigo-600 text-white shadow-md' 
                  : isLight ? 'text-slate-600 hover:text-slate-900' : 'text-slate-400 hover:text-white'
              }`}
            >
              Register
            </button>
          </div>

          {authError && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-600 rounded-lg text-sm flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              {authError}
            </div>
          )}

          <form onSubmit={handleAuth} className="space-y-4">
            <div>
              <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                isLight ? 'text-slate-500' : 'text-slate-400'
              }`}>
                Email Address
              </label>
              <input
                type="email"
                required
                value={authEmail}
                onChange={(e) => setAuthEmail(e.target.value)}
                className={`w-full border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition ${
                  isLight 
                    ? 'bg-white border-slate-300 text-slate-900 placeholder-slate-400' 
                    : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                }`}
                placeholder="user@example.com"
              />
            </div>

            <div>
              <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                isLight ? 'text-slate-500' : 'text-slate-400'
              }`}>
                Password
              </label>
              <input
                type="password"
                required
                minLength={8}
                value={authPassword}
                onChange={(e) => setAuthPassword(e.target.value)}
                className={`w-full border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 transition ${
                  isLight 
                    ? 'bg-white border-slate-300 text-slate-900 placeholder-slate-400' 
                    : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                }`}
                placeholder="••••••••"
              />
            </div>

            <button
              type="submit"
              className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-2.5 rounded-lg transition flex items-center justify-center gap-2 shadow-md shadow-indigo-500/20"
            >
              {authMode === 'login' ? <LogIn className="w-4 h-4" /> : <UserPlus className="w-4 h-4" />}
              {authMode === 'login' ? 'Sign In to Dashboard' : 'Create Account'}
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className={`min-h-screen flex flex-col font-sans transition-colors ${
      isLight ? 'bg-slate-50 text-slate-900' : 'bg-slate-950 text-slate-100'
    }`}>
      {/* Full-width sticky header */}
      <header className={`border-b sticky top-0 z-40 transition-colors ${
        isLight ? 'bg-white/90 border-slate-200 backdrop-blur' : 'bg-slate-900/90 border-slate-800 backdrop-blur'
      }`}>
        <div className="w-full px-6 lg:px-10 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-indigo-600 text-white rounded-xl shadow-sm">
              <Wallet className="w-5 h-5" />
            </div>
            <span className="font-extrabold text-xl tracking-tight text-indigo-600">
              Finance Tracker Engine
            </span>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={toggleTheme}
              className={`p-2 rounded-lg border transition ${
                isLight 
                  ? 'bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200' 
                  : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'
              }`}
              title="Toggle Theme"
            >
              {isLight ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4" />}
            </button>

            <button
              onClick={() => setShowSearchModal(true)}
              className={`border text-sm font-medium px-4 py-2 rounded-lg transition flex items-center gap-2 ${
                isLight 
                  ? 'bg-white border-slate-300 text-slate-700 hover:bg-slate-50 shadow-sm' 
                  : 'bg-slate-800 border-slate-700 text-slate-200 hover:bg-slate-700'
              }`}
            >
              <Search className="w-4 h-4 text-indigo-600" /> Search & Compare
            </button>

            <button
              onClick={() => setShowAddModal(true)}
              className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition flex items-center gap-2 shadow-sm"
            >
              <Plus className="w-4 h-4" /> Track Product
            </button>

            <button
              onClick={handleLogout}
              className={`text-sm font-medium transition ml-2 ${
                isLight ? 'text-slate-500 hover:text-slate-900' : 'text-slate-400 hover:text-white'
              }`}
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      {/* Full-width main content layout */}
      <main className="flex-1 w-full px-6 lg:px-10 py-8 grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-xl font-bold flex items-center gap-2">
              <ShoppingBag className="w-5 h-5 text-indigo-600" /> Tracked Watchlist
            </h2>
            <span className={`text-xs px-2.5 py-1 rounded-full border font-medium ${
              isLight ? 'bg-slate-200/70 border-slate-300 text-slate-700' : 'bg-slate-800 border-slate-700 text-slate-400'
            }`}>
              {products.length} Items
            </span>
          </div>

          {loading ? (
            <div className={`p-12 text-center rounded-2xl border ${
              isLight ? 'bg-white border-slate-200 text-slate-500' : 'bg-slate-900 border-slate-800 text-slate-400'
            }`}>
              Loading watchlist...
            </div>
          ) : products.length === 0 ? (
            <div className={`p-12 text-center rounded-2xl border space-y-3 ${
              isLight ? 'bg-white border-slate-200' : 'bg-slate-900 border-slate-800'
            }`}>
              <p className={isLight ? 'text-slate-500' : 'text-slate-400'}>No products tracked yet.</p>
              <button
                onClick={() => setShowAddModal(true)}
                className="text-indigo-600 text-sm font-semibold hover:underline"
              >
                + Add your first product URL
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {products.map((prod) => (
                <div
                  key={prod.id}
                  onClick={() => fetchProductHistory(prod)}
                  className={`border rounded-2xl p-5 cursor-pointer transition-all ${
                    isLight 
                      ? selectedProduct?.id === prod.id
                        ? 'bg-indigo-50/40 border-indigo-500 ring-2 ring-indigo-500/20 shadow-md'
                        : 'bg-white border-slate-200 hover:border-slate-300 shadow-sm'
                      : selectedProduct?.id === prod.id
                        ? 'bg-slate-900 border-indigo-500 ring-1 ring-indigo-500/50'
                        : 'bg-slate-900 border-slate-800 hover:border-slate-700'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex items-center gap-3">
                      <div className={`w-12 h-12 rounded-lg border flex items-center justify-center overflow-hidden shrink-0 ${
                        isLight ? 'bg-slate-100 border-slate-200' : 'bg-slate-800 border-slate-700'
                      }`}>
                        {prod.image_url ? (
                          <img src={prod.image_url} alt="" className="w-full h-full object-cover" />
                        ) : (
                          <ShoppingBag className={`w-6 h-6 ${isLight ? 'text-slate-400' : 'text-slate-500'}`} />
                        )}
                      </div>
                      <div>
                        <h3 className={`font-semibold text-sm line-clamp-1 ${isLight ? 'text-slate-900' : 'text-slate-100'}`}>
                          {prod.title}
                        </h3>
                        <p className={`text-xs ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>
                          Added {new Date(prod.created_at).toLocaleDateString()}
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-1">
                      <button
                        onClick={(e) => handleRefresh(prod.id, e)}
                        className={`p-1.5 rounded-lg transition ${
                          isLight ? 'text-slate-400 hover:text-slate-700 hover:bg-slate-100' : 'text-slate-400 hover:text-white hover:bg-slate-800'
                        }`}
                        title="Trigger manual refresh"
                      >
                        <RefreshCw
                          className={`w-4 h-4 ${refreshingId === prod.id ? 'animate-spin text-indigo-600' : ''}`}
                        />
                      </button>
                      <button
                        onClick={(e) => handleDelete(prod.id, e)}
                        className={`p-1.5 rounded-lg transition ${
                          isLight ? 'text-slate-400 hover:text-red-600 hover:bg-red-50' : 'text-slate-400 hover:text-red-400 hover:bg-slate-800'
                        }`}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  <div className={`grid grid-cols-2 gap-2 pt-3 border-t text-xs ${
                    isLight ? 'border-slate-100' : 'border-slate-800/80'
                  }`}>
                    <div className={`p-2 rounded-lg border ${
                      isLight ? 'bg-slate-50 border-slate-200/60' : 'bg-slate-950/60 border-slate-800/60'
                    }`}>
                      <span className={`block mb-0.5 ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>Amazon Price</span>
                      <span className="font-bold text-emerald-600">
                        {(() => {
                          const price = prod.resolved_amazon_price 
                            ?? prod.amazon_price 
                            ?? prod.initial_amazon_price 
                            ?? prod.current_amazon_price 
                            ?? prod.latest_amazon_price;
                          return price !== null && price !== undefined && !isNaN(price)
                            ? `₹${Number(price).toLocaleString()}` 
                            : 'N/A';
                        })()}
                      </span>
                    </div>

                    <div className={`p-2 rounded-lg border ${
                      isLight ? 'bg-slate-50 border-slate-200/60' : 'bg-slate-950/60 border-slate-800/60'
                    }`}>
                      <span className={`block mb-0.5 ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>Flipkart Price</span>
                      <span className="font-bold text-blue-600">
                        {(() => {
                          const price = prod.resolved_flipkart_price 
                            ?? prod.flipkart_price 
                            ?? prod.initial_flipkart_price 
                            ?? prod.current_flipkart_price 
                            ?? prod.latest_flipkart_price;
                          return price !== null && price !== undefined && !isNaN(price)
                            ? `₹${Number(price).toLocaleString()}` 
                            : 'N/A';
                        })()}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-6">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <BarChart2 className="w-5 h-5 text-indigo-600" /> Price Analytics
          </h2>

          {selectedProduct ? (
            <div className={`border rounded-2xl p-6 space-y-6 sticky top-24 transition-colors ${
              isLight ? 'bg-white border-slate-200 shadow-sm' : 'bg-slate-900 border-slate-800'
            }`}>
              <div>
                <span className="text-xs font-semibold text-indigo-600 uppercase tracking-wider">
                  Selected Product
                </span>
                <h3 className={`text-lg font-bold mt-1 ${isLight ? 'text-slate-900' : 'text-white'}`}>
                  {selectedProduct.title}
                </h3>
              </div>

              {productHistory && (
                <>
                  <div className="grid grid-cols-3 gap-2">
                    <div className={`p-3 rounded-xl border text-center ${
                      isLight ? 'bg-slate-50 border-slate-200' : 'bg-slate-950 border-slate-800/80'
                    }`}>
                      <span className={`text-[10px] uppercase tracking-wider block ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>Lowest</span>
                      <span className="text-sm font-bold text-emerald-600">
                        {productHistory.lowest_price ? `₹${productHistory.lowest_price.toLocaleString()}` : 'N/A'}
                      </span>
                    </div>
                    <div className={`p-3 rounded-xl border text-center ${
                      isLight ? 'bg-slate-50 border-slate-200' : 'bg-slate-950 border-slate-800/80'
                    }`}>
                      <span className={`text-[10px] uppercase tracking-wider block ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>Highest</span>
                      <span className="text-sm font-bold text-rose-600">
                        {productHistory.highest_price ? `₹${productHistory.highest_price.toLocaleString()}` : 'N/A'}
                      </span>
                    </div>
                    <div className={`p-3 rounded-xl border text-center ${
                      isLight ? 'bg-slate-50 border-slate-200' : 'bg-slate-950 border-slate-800/80'
                    }`}>
                      <span className={`text-[10px] uppercase tracking-wider block ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>Average</span>
                      <span className="text-sm font-bold text-indigo-600">
                        {productHistory.average_price ? `₹${productHistory.average_price.toLocaleString()}` : 'N/A'}
                      </span>
                    </div>
                  </div>

                  <div className="h-64 w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={productHistory.chart_data}>
                        <CartesianGrid strokeDasharray="3 3" stroke={isLight ? '#e2e8f0' : '#334155'} />
                        <XAxis
                          dataKey="timestamp"
                          stroke={isLight ? '#64748b' : '#94a3b8'}
                          tickFormatter={(t) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                          fontSize={11}
                        />
                        <YAxis stroke={isLight ? '#64748b' : '#94a3b8'} fontSize={11} domain={['auto', 'auto']} />
                        <Tooltip
                          contentStyle={{ 
                            backgroundColor: isLight ? '#ffffff' : '#0f172a', 
                            borderColor: isLight ? '#e2e8f0' : '#334155', 
                            borderRadius: '8px',
                            color: isLight ? '#0f172a' : '#ffffff'
                          }}
                          labelFormatter={(t) => new Date(t).toLocaleString()}
                        />
                        <Legend />
                        <Line
                          type="monotone"
                          dataKey="amazon_price"
                          name="Amazon"
                          stroke="#10b981"
                          strokeWidth={2}
                          dot={{ r: 3 }}
                        />
                        <Line
                          type="monotone"
                          dataKey="flipkart_price"
                          name="Flipkart"
                          stroke="#3b82f6"
                          strokeWidth={2}
                          dot={{ r: 3 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>

                  <button
                    onClick={() => setShowAlertModal(true)}
                    className={`w-full py-2.5 rounded-xl transition flex items-center justify-center gap-2 text-sm font-semibold border ${
                      isLight 
                        ? 'bg-slate-100 hover:bg-slate-200 text-slate-800 border-slate-300' 
                        : 'bg-slate-800 hover:bg-slate-700 text-slate-200 border-slate-700'
                    }`}
                  >
                    <Bell className="w-4 h-4 text-indigo-600" /> Set Price Drop Alert
                  </button>
                </>
              )}
            </div>
          ) : (
            <div className={`p-12 text-center rounded-2xl border space-y-2 ${
              isLight ? 'bg-white border-slate-200 text-slate-500' : 'bg-slate-900 border-slate-800 text-slate-400'
            }`}>
              <BarChart2 className={`w-8 h-8 mx-auto mb-2 ${isLight ? 'text-slate-300' : 'text-slate-600'}`} />
              <p>Select a product card to inspect historical pricing analytics and charts.</p>
            </div>
          )}
        </div>
      </main>

      {/* Live Search & Compare Modal */}
      {showSearchModal && (
        <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className={`border rounded-2xl w-full max-w-xl p-6 shadow-2xl space-y-4 ${
            isLight ? 'bg-white border-slate-200 text-slate-900' : 'bg-slate-900 border-slate-800 text-white'
          }`}>
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold flex items-center gap-2">
                <Search className="w-5 h-5 text-indigo-600" /> Live Search & Compare
              </h3>
              <button 
                onClick={() => setShowSearchModal(false)}
                className={`text-sm ${isLight ? 'text-slate-400 hover:text-slate-700' : 'text-slate-400 hover:text-white'}`}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSearch} className="flex gap-2">
              <input
                type="text"
                required
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search product (e.g. iPhone 15, Sony WH-1000XM5)..."
                className={`flex-1 border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                  isLight 
                    ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                    : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                }`}
              />
              <button
                type="submit"
                disabled={searchLoading}
                className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition"
              >
                {searchLoading ? 'Scraping...' : 'Search'}
              </button>
            </form>

            {searchResults && (
              <div className={`border rounded-xl p-4 space-y-3 mt-4 ${
                isLight ? 'bg-slate-50 border-slate-200' : 'bg-slate-950 border-slate-800'
              }`}>
                <h4 className="font-semibold text-sm">
                  {searchResults.flipkart?.title || searchResults.amazon?.title || searchResults.query}
                </h4>
                
                <div className="grid grid-cols-2 gap-3 text-xs">
                  <div className={`p-3 rounded-lg border ${
                    isLight ? 'bg-white border-slate-200' : 'bg-slate-900 border-slate-800'
                  }`}>
                    <span className={`block mb-1 ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>Amazon Price</span>
                    <span className="text-base font-bold text-emerald-600">
                      {searchResults.amazon?.price ? `₹${searchResults.amazon.price.toLocaleString()}` : 'N/A'}
                    </span>
                  </div>
                  <div className={`p-3 rounded-lg border ${
                    isLight ? 'bg-white border-slate-200' : 'bg-slate-900 border-slate-800'
                  }`}>
                    <span className={`block mb-1 ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>Flipkart Price</span>
                    <span className="text-base font-bold text-blue-600">
                      {searchResults.flipkart?.price ? `₹${searchResults.flipkart.price.toLocaleString()}` : 'N/A'}
                    </span>
                  </div>
                </div>

                {searchResults.cheaper_platform && (
                  <div className={`text-xs p-2.5 rounded-lg border ${
                    isLight 
                      ? 'bg-amber-50 border-amber-200 text-amber-800' 
                      : 'bg-amber-950/40 border-amber-500/30 text-amber-400'
                  }`}>
                    💡 Cheaper option available on <strong>{searchResults.cheaper_platform}</strong>!
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Manual Track Product Modal */}
      {showAddModal && (
        <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className={`border rounded-2xl w-full max-w-lg p-6 shadow-2xl space-y-4 ${
            isLight ? 'bg-white border-slate-200 text-slate-900' : 'bg-slate-900 border-slate-800 text-white'
          }`}>
            <h3 className="text-lg font-bold flex items-center gap-2">
              <Plus className="w-5 h-5 text-indigo-600" /> Track New Product
            </h3>
            <form onSubmit={handleAddProduct} className="space-y-4">
              <div>
                <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                  isLight ? 'text-slate-500' : 'text-slate-400'
                }`}>
                  Product Title *
                </label>
                <input
                  type="text"
                  required
                  value={formData.title}
                  onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                  placeholder="e.g. Apple iPhone 15 (128 GB) - Blue"
                  className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                    isLight 
                      ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                      : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                  }`}
                />
              </div>

              <div>
                <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                  isLight ? 'text-slate-500' : 'text-slate-400'
                }`}>
                  Image URL (Optional)
                </label>
                <input
                  type="url"
                  value={formData.image_url}
                  onChange={(e) => setFormData({ ...formData, image_url: e.target.value })}
                  placeholder="https://..."
                  className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                    isLight 
                      ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                      : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                  }`}
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                    isLight ? 'text-slate-500' : 'text-slate-400'
                  }`}>
                    Amazon URL
                  </label>
                  <input
                    type="url"
                    value={formData.amazon_url}
                    onChange={(e) => setFormData({ ...formData, amazon_url: e.target.value })}
                    placeholder="https://amazon.in/..."
                    className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                      isLight 
                        ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                        : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                    }`}
                  />
                </div>
                <div>
                  <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                    isLight ? 'text-slate-500' : 'text-slate-400'
                  }`}>
                    Amazon Initial Price (₹)
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={formData.initial_amazon_price}
                    onChange={(e) => setFormData({ ...formData, initial_amazon_price: e.target.value })}
                    placeholder="79900"
                    className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                      isLight 
                        ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                        : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                    }`}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                    isLight ? 'text-slate-500' : 'text-slate-400'
                  }`}>
                    Flipkart URL
                  </label>
                  <input
                    type="url"
                    value={formData.flipkart_url}
                    onChange={(e) => setFormData({ ...formData, flipkart_url: e.target.value })}
                    placeholder="https://flipkart.com/..."
                    className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                      isLight 
                        ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                        : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                    }`}
                  />
                </div>
                <div>
                  <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                    isLight ? 'text-slate-500' : 'text-slate-400'
                  }`}>
                    Flipkart Initial Price (₹)
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={formData.initial_flipkart_price}
                    onChange={(e) => setFormData({ ...formData, initial_flipkart_price: e.target.value })}
                    placeholder="78990"
                    className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                      isLight 
                        ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                        : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                    }`}
                  />
                </div>
              </div>

              <div className={`flex items-center justify-end gap-3 pt-4 border-t ${
                isLight ? 'border-slate-100' : 'border-slate-800'
              }`}>
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className={`px-4 py-2 text-sm font-medium ${
                    isLight ? 'text-slate-500 hover:text-slate-800' : 'text-slate-400 hover:text-white'
                  }`}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition shadow-sm"
                >
                  Save & Track
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Set Price Alert Modal */}
      {showAlertModal && (
        <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className={`border rounded-2xl w-full max-w-sm p-6 shadow-2xl space-y-4 ${
            isLight ? 'bg-white border-slate-200 text-slate-900' : 'bg-slate-900 border-slate-800 text-white'
          }`}>
            <h3 className="text-lg font-bold flex items-center gap-2">
              <Bell className="w-5 h-5 text-indigo-600" /> Price Drop Alert
            </h3>
            <p className={`text-xs ${isLight ? 'text-slate-500' : 'text-slate-400'}`}>
              Set a target price threshold for <strong className={isLight ? 'text-slate-800' : 'text-slate-200'}>{selectedProduct?.title}</strong>.
            </p>
            <form onSubmit={handleCreateAlert} className="space-y-4">
              <div>
                <label className={`block text-xs font-semibold uppercase tracking-wider mb-1 ${
                  isLight ? 'text-slate-500' : 'text-slate-400'
                }`}>
                  Target Price (₹)
                </label>
                <input
                  type="number"
                  required
                  value={alertTargetPrice}
                  onChange={(e) => setAlertTargetPrice(e.target.value)}
                  placeholder="e.g. 75000"
                  className={`w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
                    isLight 
                      ? 'bg-slate-50 border-slate-300 text-slate-900 placeholder-slate-400' 
                      : 'bg-slate-950 border-slate-800 text-white placeholder-slate-600'
                  }`}
                />
              </div>

              <div className="flex items-center justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAlertModal(false)}
                  className={`px-4 py-2 text-sm font-medium ${
                    isLight ? 'text-slate-500 hover:text-slate-800' : 'text-slate-400 hover:text-white'
                  }`}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition shadow-sm"
                >
                  Set Alert
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}