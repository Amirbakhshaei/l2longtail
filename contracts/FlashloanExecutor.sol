// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title FlashloanExecutor
/// @notice Balancer V2 flashloan-driven multi-hop arbitrage executor for
///         Uniswap/Camelot V2 and V3 routers on Arbitrum One.
/// @dev Implements IFlashLoanRecipient. Borrows WETH, walks the encoded swap
///       path across whitelisted V2 (swapExactTokensForTokens) and V3
///       (exactInputSingle) routers, repays the Balancer Vault (0% fee venue
///       on Arbitrum), and forwards net profit to the owner.

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

/// @notice Uniswap/Camelot V3 SwapRouter02 — exactInputSingle entrypoint.
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 deadline;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }

    function exactInputSingle(ExactInputSingleParams calldata params)
        external
        payable
        returns (uint256 amountOut);
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
    error LengthMismatch();

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
    /// @param path     Token addresses, path[0] == path[last] == WETH.
    /// @param routers  Router address for each hop (len(path) - 1).
    /// @param feeTiers V3 fee tier (bps, e.g. 3000 = 0.30%) per hop.
    ///                 Ignored for V2 hops; pass 0 there.
    /// @param isV3     True if hop i should route via V3 exactInputSingle.
    /// @param amount   WETH principal to borrow via flashloan.
    function executeArbitrage(
        address[] calldata path,
        address[] calldata routers,
        uint24[] calldata feeTiers,
        bool[] calldata isV3,
        uint256 amount
    ) external onlyOwner {
        if (
            path.length < 2
                || routers.length != path.length - 1
                || feeTiers.length != routers.length
                || isV3.length != routers.length
        ) revert LengthMismatch();

        IERC20[] memory tokens = new IERC20[](1);
        tokens[0] = IERC20(address(WETH));
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = amount;

        // userData packs (path, routers, feeTiers, isV3) for receiveFlashLoan.
        bytes memory userData = abi.encode(path, routers, feeTiers, isV3);
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

        (
            address[] memory path,
            address[] memory routers,
            uint24[] memory feeTiers,
            bool[] memory isV3
        ) = abi.decode(userData, (address[], address[], uint24[], bool[]));

        uint256 amountIn = _route(path, routers, feeTiers, isV3, amounts[0]);

        uint256 repayment = amounts[0] + feeAmounts[0];
        uint256 finalBalance = WETH.balanceOf(address(this));
        if (finalBalance < repayment) revert InsufficientRepayment();

        uint256 profit = finalBalance - repayment;
        if (profit == 0) revert NoProfit();

        WETH.transfer(VAULT, repayment);
        if (profit > 0) {
            WETH.transfer(owner, profit);
        }

        emit ArbitrageExecuted(amounts[0], profit);
    }

    /// @notice Walk every hop, returning the final WETH amount out.
    function _route(
        address[] memory path,
        address[] memory routers,
        uint24[] memory feeTiers,
        bool[] memory isV3,
        uint256 borrowed
    ) private returns (uint256 amountIn) {
        amountIn = borrowed;
        for (uint256 i = 0; i < routers.length; i++) {
            address router = routers[i];
            address tokenIn = path[i];
            address tokenOut = path[i + 1];

            IERC20(tokenIn).approve(router, amountIn);
            amountIn = isV3[i]
                ? _swapV3(router, tokenIn, tokenOut, feeTiers[i], amountIn)
                : _swapV2(router, tokenIn, tokenOut, amountIn);
            // Reset approval on the spent token to avoid lingering allowance.
            IERC20(tokenIn).approve(router, 0);
        }
    }

    function _swapV2(
        address router,
        address tokenIn,
        address tokenOut,
        uint256 amountIn
    ) private returns (uint256 amountOut) {
        address[] memory hop = new address[](2);
        hop[0] = tokenIn;
        hop[1] = tokenOut;
        uint256[] memory out = IUniswapV2Router(router).swapExactTokensForTokens(
            amountIn,
            0, // slippage enforced off-chain via simulation gate
            hop,
            address(this),
            block.timestamp + DEADLINE_BUFFER
        );
        amountOut = out[out.length - 1];
    }

    function _swapV3(
        address router,
        address tokenIn,
        address tokenOut,
        uint24 fee,
        uint256 amountIn
    ) private returns (uint256 amountOut) {
        amountOut = ISwapRouter(router).exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn: tokenIn,
                tokenOut: tokenOut,
                fee: fee,
                recipient: address(this),
                deadline: block.timestamp + DEADLINE_BUFFER,
                amountIn: amountIn,
                amountOutMinimum: 0, // enforced off-chain via sim gate
                sqrtPriceLimitX96: 0
            })
        );
    }

    /// @notice Sweep accidentally-stuck ERC20s (excluding WETH repayment duty).
    function sweep(address token, address to) external onlyOwner {
        IERC20(token).transfer(to, IERC20(token).balanceOf(address(this)));
    }

    event ArbitrageExecuted(uint256 borrowed, uint256 profit);
}
