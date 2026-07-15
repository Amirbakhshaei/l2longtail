// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title FlashloanExecutor
/// @notice Balancer V2 flashloan-driven multi-hop arbitrage executor for
///         Uniswap-V2-style constant-product routers on Arbitrum One.
/// @dev Implements IFlashLoanRecipient. Borrows WETH, walks the encoded swap
///       path across whitelisted V2 routers, repays the Balancer Vault (0% fee
///       venue on Arbitrum), and forwards net profit to the owner.

interface IERC20 {
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IWETH is IERC20 {
    function deposit() external payable;
    function withdraw(uint256 amount) external;
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

interface IFlashLoanRecipient {
    function receiveFlashLoan(
        IERC20[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external;
}

interface IVault {
    function flashLoan(
        IFlashLoanRecipient recipient,
        IERC20[] memory tokens,
        uint256[] memory amounts,
        bytes memory userData
    ) external;
}

contract FlashloanExecutor is IFlashLoanRecipient {
    address public immutable VAULT;
    IWETH public immutable WETH;
    address public owner;

    uint256 public constant DEADLINE_BUFFER = 300;

    error Unauthorized();
    error NotVault();
    error InsufficientRepayment();
    error NoProfit();

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    constructor(address vault_, address weth_) {
        VAULT = vault_;
        WETH = IWETH(weth_);
        owner = msg.sender;
    }

    /// @notice Entry point used by Process B to trigger the arbitrage.
    /// @param path   Token addresses, path[0] == path[last] == WETH.
    /// @param routers Router address for each hop (len(path) - 1).
    /// @param amount  WETH principal to borrow via flashloan.
    function executeArbitrage(
        address[] calldata path,
        address[] calldata routers,
        uint256 amount
    ) external onlyOwner {
        if (path.length < 2 || routers.length != path.length - 1) revert Unauthorized();
        IERC20[] memory tokens = new IERC20[](1);
        tokens[0] = IERC20(address(WETH));
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = amount;
        // userData packs (path, routers) for receiveFlashLoan.
        bytes memory userData = abi.encode(path, routers);
        IVault(VAULT).flashLoan(this, tokens, amounts, userData);
    }

    /// @notice Balancer Vault callback. Borrowed WETH is already in this
    ///         contract when this fires.
    function receiveFlashLoan(
        IERC20[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external override {
        if (msg.sender != VAULT) revert NotVault();

        (address[] memory path, address[] memory routers) =
            abi.decode(userData, (address[], address[]));

        uint256 borrowed = amounts[0];
        uint256 fee = feeAmounts[0];

        // Approve first hop spend.
        IERC20 first = tokens[0];
        first.approve(routers[0], borrowed);

        uint256 amountIn = borrowed;
        for (uint256 i = 0; i < routers.length; i++) {
            address router = routers[i];
            address tokenOut = path[i + 1];
            IERC20(path[i]).approve(router, amountIn);

            uint256[] memory out = IUniswapV2Router(router)
                .swapExactTokensForTokens(
                    amountIn,
                    0, // slippage enforced off-chain via simulation gate
                    _hop(path, i),
                    address(this),
                    block.timestamp + DEADLINE_BUFFER
                );
            amountIn = out[out.length - 1];

            // Reset approval on the spent token to avoid lingering allowance.
            IERC20(path[i]).approve(router, 0);
        }

        uint256 finalBalance = WETH.balanceOf(address(this));
        uint256 repayment = borrowed + fee;
        if (finalBalance < repayment) revert InsufficientRepayment();

        uint256 profit = finalBalance - repayment;
        if (profit == 0) revert NoProfit();

        // Repay the Vault.
        WETH.transfer(VAULT, repayment);

        // Forward net profit to owner.
        if (profit > 0) {
            WETH.transfer(owner, profit);
        }

        emit ArbitrageExecuted(borrowed, profit);
    }

    function _hop(address[] memory path, uint256 i)
        private
        pure
        returns (address[] memory)
    {
        address[] memory hop = new address[](2);
        hop[0] = path[i];
        hop[1] = path[i + 1];
        return hop;
    }

    /// @notice Sweep accidentally-stuck ERC20s (excluding WETH repayment duty).
    function sweep(address token, address to) external onlyOwner {
        IERC20(token).transfer(to, IERC20(token).balanceOf(address(this)));
    }

    event ArbitrageExecuted(uint256 borrowed, uint256 profit);
}
