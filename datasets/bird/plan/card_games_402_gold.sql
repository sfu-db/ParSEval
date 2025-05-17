{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "TIMES",
      "operator": "*",
      "type": "DECIMAL",
      "operands": [
        {
          "kind": "DIVIDE",
          "operator": "/",
          "type": "DECIMAL",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "DECIMAL"
            },
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "BIGINT",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "BIGINT"
                    },
                    {
                      "kind": "LITERAL",
                      "value": 0,
                      "type": "BIGINT",
                      "nullable": false,
                      "precision": 19
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "BIGINT",
                  "nullable": true,
                  "precision": 19
                },
                {
                  "kind": "CAST",
                  "operator": "CAST",
                  "type": "BIGINT",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "BIGINT"
                    }
                  ]
                }
              ]
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": 100,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
        }
      ]
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "DECIMAL"
            }
          ],
          "type": "DECIMAL",
          "name": null
        },
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [],
          "type": "BIGINT",
          "name": null
        }
      ],
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "DECIMAL",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 19,
                      "name": "$19",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "+3",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": 1.0,
                  "type": "DECIMAL",
                  "nullable": false,
                  "precision": 11
                },
                {
                  "kind": "LITERAL",
                  "value": 0.0,
                  "type": "DECIMAL",
                  "nullable": false,
                  "precision": 11
                }
              ]
            },
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            }
          ],
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 17,
                    "name": "$17",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "legendary",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              "variableset": "[]",
              "id": "1",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "cards",
                  "id": "0",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}