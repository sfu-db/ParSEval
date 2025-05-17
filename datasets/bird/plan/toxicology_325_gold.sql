{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "REAL",
      "operands": [
        {
          "kind": "TIMES",
          "operator": "*",
          "type": "REAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "BIGINT"
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
    }
  ],
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "VARCHAR"
            }
          ],
          "type": "BIGINT",
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
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "VARCHAR",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 2,
                      "name": "$2",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "-",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "CAST",
                  "operator": "CAST",
                  "type": "VARCHAR",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 0,
                      "name": "$0",
                      "type": "VARCHAR"
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "VARCHAR",
                  "nullable": true,
                  "precision": -1
                }
              ]
            },
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "VARCHAR"
            }
          ],
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "bond",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}