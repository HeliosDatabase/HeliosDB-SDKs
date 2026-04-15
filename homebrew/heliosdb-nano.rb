class HeliosdbNano < Formula
  desc "PostgreSQL & MySQL compatible embedded database with vector search"
  homepage "https://github.com/Dimensigon/HDB-HeliosDB-Nano"
  version "3.10.0"
  license "AGPL-3.0-only"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/Dimensigon/HDB-HeliosDB-Nano/releases/download/v3.10.0/heliosdb-nano-aarch64-apple-darwin.tar.gz"
      sha256 "PLACEHOLDER_SHA256_ARM64"
    else
      url "https://github.com/Dimensigon/HDB-HeliosDB-Nano/releases/download/v3.10.0/heliosdb-nano-x86_64-apple-darwin.tar.gz"
      sha256 "PLACEHOLDER_SHA256_X64"
    end
  end

  on_linux do
    url "https://github.com/Dimensigon/HDB-HeliosDB-Nano/releases/download/v3.10.0/heliosdb-nano-x86_64-unknown-linux-gnu.tar.gz"
    sha256 "PLACEHOLDER_SHA256_LINUX"
  end

  def install
    bin.install "heliosdb-nano"
  end

  test do
    assert_match "HeliosDB", shell_output("#{bin}/heliosdb-nano --version 2>&1", 0)
  end
end
