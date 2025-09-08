{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 5,
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "VARCHAR"
        },
        {
          "kind": "PLUS",
          "operator": "+",
          "type": "INTEGER",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 1,
              "name": "$1",
              "type": "INTEGER"
            },
            {
              "kind": "INPUT_REF",
              "index": 2,
              "name": "$2",
              "type": "INTEGER"
            }
          ]
        }
      ],
      "id": "5",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "VARCHAR"
            }
          ],
          "aggs": [
            {
              "operator": "SUM",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 1,
                  "type": "INTEGER"
                }
              ],
              "type": "INTEGER",
              "name": null
            },
            {
              "operator": "SUM",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 2,
                  "type": "INTEGER"
                }
              ],
              "type": "INTEGER",
              "name": null
            }
          ],
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 12,
                  "name": "$12",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 13,
                  "name": "$13",
                  "type": "INTEGER"
                }
              ],
              "id": "3",
              "inputs": [
                {
                  "relOp": "LogicalJoin",
                  "joinType": "inner",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 5,
                        "name": "$5",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "League",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "Match",
                      "id": "1",
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
  ]
}