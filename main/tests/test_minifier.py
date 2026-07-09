from agents.minifier import minify_solidity


def test_minifier_strips_comments() -> None:
    source = """
    // This is a comment
    pragma solidity ^0.8.0;
    contract Foo {
        /* block comment */
        function bar() public {}
    }
    """
    result = minify_solidity(source)
    assert "//" not in result
    assert "/*" not in result
    assert "*/" not in result


def test_minifier_removes_pragma() -> None:
    source = "pragma solidity ^0.8.19; contract Test {}"
    result = minify_solidity(source)
    assert "pragma" not in result


def test_minifier_removes_interface_abstract() -> None:
    source = "interface IERC20 { function totalSupply() external view returns (uint256); }"
    result = minify_solidity(source)
    assert "interface" not in result


def test_minifier_collapses_whitespace() -> None:
    source = "contract   Foo   {\n\n\n    function   bar()   public   {}\n\n}"
    result = minify_solidity(source)
    assert "  " not in result
    assert "\n" not in result
    assert "\t" not in result


def test_minifier_empty_input() -> None:
    assert minify_solidity("") == ""


def test_minifier_compression_ratio() -> None:
    source = """
    // SPDX-License-Identifier: MIT
    // This file implements a standard ERC20 token contract
    // with additional features for governance and staking
    pragma solidity ^0.8.19;

    /*
     * This is a very long block comment that should be removed entirely
     * by the minifier to reduce the token count significantly.
     * It spans multiple lines and contains lots of text.
     * We need enough verbose content to ensure the minifier
     * achieves at least 60% compression ratio on this sample.
     * Comments and whitespace should make up the majority of
     * the original source code size.
     */

    // Import OpenZeppelin libraries for safe math operations
    // and access control patterns
    // These are standard development practices in Solidity

    interface IERC20 {
        function totalSupply() external view returns (uint256);
        function balanceOf(address account) external view returns (uint256);
        function transfer(address to, uint256 amount) external returns (bool);
    }

    // This contract extends the base token with additional functionality
    // It includes staking rewards and governance voting mechanisms
    abstract contract BaseToken {
        // Internal state variable for tracking balances
        // This mapping is used by all transfer functions
        mapping(address => uint256) internal _balances;

        /* Another block comment that adds size without adding logic */
        function _mint(address to, uint256 amount) internal virtual {
            _balances[to] += amount;
        }
    }

    // Main token contract implementing all public-facing functions
    // This contract is the primary entry point for token interactions
    contract MyToken is BaseToken {
        // Token name as a public string variable
        // Used by wallets and block explorers for display
        string public name = "MyToken";

        // Token symbol as a public string variable
        // Used by wallets and exchanges for identification
        string public symbol = "MTK";

        /* Decimals of precision for the token */
        uint8 public decimals = 18;

        // Transfer function with balance validation
        // Includes checks for sufficient balance before executing
        function transfer(address to, uint256 amount) public returns (bool) {
            // Check sender balance before processing transfer
            require(_balances[msg.sender] >= amount, "Insufficient balance");
            _balances[msg.sender] -= amount;
            _balances[to] += amount;
            return true;
        }
    }
    """
    result = minify_solidity(source)
    ratio = (1 - len(result) / len(source)) * 100
    assert ratio >= 60, f"Compression ratio {ratio:.1f}% is below 60%"
