class HeliosdbNano < Formula
  desc "PostgreSQL & MySQL compatible embedded database with vector search"
  homepage "https://github.com/HeliosDatabase/HeliosDB-Nano"
  version "4.41.0"
  license "Apache-2.0"

  # Prebuilt binaries are published for Apple Silicon macOS and for Linux
  # (x86_64, aarch64). There is no Intel macOS build; on that platform build
  # from source instead: `cargo install heliosdb-nano`.
  on_macos do
    on_arm do
      url "https://github.com/HeliosDatabase/HeliosDB-Nano/releases/download/v4.41.0/heliosdb-nano-v4.41.0-aarch64-apple-darwin.tar.gz"
      sha256 "f5ba2f6f8caca2ce6d7168ebe0dd3c17c781e4ca23603bb9e15a96f4285f33f7"
    end
  end

  on_linux do
    on_intel do
      url "https://github.com/HeliosDatabase/HeliosDB-Nano/releases/download/v4.41.0/heliosdb-nano-v4.41.0-x86_64-unknown-linux-gnu.tar.gz"
      sha256 "b774b0a78984e0c07cc550c278dd8d107de1a11db37e79d3d79e9f9f081d2eb0"
    end
    on_arm do
      url "https://github.com/HeliosDatabase/HeliosDB-Nano/releases/download/v4.41.0/heliosdb-nano-v4.41.0-aarch64-unknown-linux-gnu.tar.gz"
      sha256 "48fae67e0dd3819174073f1ec2306a7684e2b6b3d8b48993d854a960a67efd80"
    end
  end

  def install
    bin.install "heliosdb-nano"
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/heliosdb-nano --version 2>&1")
  end
end
